import asyncio
import json
import time
import unittest
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
from hue_agent.cloud_link import CloudLink, validate_url


class FakeController:
    session_id = 'process1'
    ready = True
    calls = []
    async def execute(self, data, *, context):
        self.calls.append((data, context))
        context.require_current()
        return {'commandId':data['commandId'], 'status':'offline', 'errorCode':'BLE_OFFLINE'}


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
    async def recv(self):
        value = await self.incoming.get()
        if isinstance(value, Exception):
            raise value
        return value
    async def send(self, value):
        self.sent.append(value)
    async def close(self):
        await self.incoming.put(ConnectionError('closed'))


class CloudLinkTests(unittest.IsolatedAsyncioTestCase):
    async def test_handshake_uses_server_clock_despite_local_skew(self):
        with patch('hue_agent.cloud_link.time.time',return_value=100):
            task=await self.session(skew=2.5)
            self.assertTrue(self.link.current['live'])
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    async def asyncSetUp(self):
        self.controller = FakeController()
        self.controller.calls = []
        self.link = CloudLink(self.controller, {})
        self.ws = Socket()

    async def until_sent(self, number):
        async with asyncio.timeout(1):
            while len(self.ws.sent) < number:
                await asyncio.sleep(0.001)

    async def session(self, skew=0):
        await self.ws.incoming.put(json.dumps({'v':1,'type':'hello','sessionId':'link1','serverTime':time.time()+skew}))
        task = asyncio.create_task(self.link.session(self.ws))
        await self.until_sent(1)
        await self.ws.incoming.put(json.dumps({'v':1,'type':'ready_ack','sessionId':'link1','serverTime':time.time()}))
        await asyncio.sleep(0.005)
        return task

    def command(self):
        return dict(commandId='11111111-1111-4111-8111-111111111111',deviceId='hue-lwa017-01',sessionId='link1',sequence=1,expiresAt=time.time()+5,operation='query')

    async def test_hello_binds_cloud_session_not_process_and_offline_is_preserved(self):
        task = await self.session()
        command = self.command()
        await self.ws.incoming.put(json.dumps(dict(v=1,type='command',sessionId='link1',commandId=command['commandId'],command=command)))
        await self.until_sent(2)
        packet = json.loads(self.ws.sent[1])
        self.assertEqual(packet['result']['status'], 'offline')
        self.assertEqual(self.controller.calls[0][1].session_id, 'link1')
        self.assertEqual(self.controller.session_id, 'process1')
        await self.ws.close()
        with self.assertRaises(ConnectionError):
            await task
        self.assertFalse(self.controller.calls[0][1].is_current())

    async def test_wrong_session_binary_and_oversized_messages_do_not_execute(self):
        for invalid in (b'{}', ' '*8193, json.dumps({'v':1,'type':'command','sessionId':'old'}), '{'):
            self.ws = Socket()
            task = await self.session()
            await self.ws.incoming.put(invalid)
            with self.assertRaises(ValueError):
                await task
        self.assertEqual(self.controller.calls, [])

    async def test_invalid_server_clock_rejected_before_ready(self):
        await self.ws.incoming.put(json.dumps({'v':1,'type':'hello','sessionId':'link1','serverTime':float('nan')}))
        with self.assertRaisesRegex(ValueError,'INVALID_MESSAGE'):
            await self.link.session(self.ws)
        self.assertEqual(self.ws.sent, [])

    async def test_shutdown_invalidates_context_and_drains_tasks(self):
        task = await self.session()
        await self.link.close()
        with self.assertRaises(ConnectionError):
            await task
        self.assertTrue(self.link.stopped.is_set())

    async def test_missing_pong_closes_and_invalidates_the_link(self):
        with patch('hue_agent.cloud_link.HEARTBEAT_SECONDS',0.01,create=True), patch('hue_agent.cloud_link.PONG_SECONDS',0.01,create=True):
            task=await self.session()
            with self.assertRaises(ConnectionError):
                await asyncio.wait_for(task,0.3)

    async def test_failed_handshakes_back_off_and_stable_session_resets_delay(self):
        pauses=[]
        class Connection:
            async def __aenter__(self):return self.ws
            async def __aexit__(self,*args):pass
        connection=Connection();connection.ws=self.ws
        module=ModuleType('websockets.asyncio.client')
        module.connect=lambda *args,**kwargs:connection
        calls=0
        async def session(ws):
            nonlocal calls
            calls+=1
            if calls==3:self.link.ready_since=time.monotonic()-31
            raise ValueError('CLOCK_SKEW')
        async def wait(awaitable,timeout):
            awaitable.close()
            pauses.append(timeout)
            if len(pauses)==3:self.link.stopped.set();return True
            raise TimeoutError()
        self.link.config={'url':'wss://relay.example/agent','token_file':'test-only'}
        with patch.dict(sys.modules,{'websockets.asyncio.client':module}), patch.object(self.link,'session',session), patch('hue_agent.cloud_link.Path.stat',return_value=SimpleNamespace(st_mode=0o100600)), patch('hue_agent.cloud_link.Path.read_text',return_value='a'*64), patch('hue_agent.cloud_link.random.uniform',side_effect=lambda lower,upper:upper), patch('hue_agent.cloud_link.asyncio.wait_for',side_effect=wait):
            await self.link.run()
        self.assertEqual(pauses,[1,2,1])

    def test_url_requires_tls_and_no_credentials_query_or_fragment(self):
        self.assertEqual(validate_url('wss://relay.example/agent'),'wss://relay.example/agent')
        for url in ('ws://relay.example/agent','wss://u:p@relay.example/agent','wss://relay.example/agent?token=secret','wss://relay.example/agent#x'):
            with self.assertRaises(ValueError):validate_url(url)
