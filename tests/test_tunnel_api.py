import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from hue_agent.agent import Controller
from hue_agent.journal import Journal
from hue_agent.lamp import Lamp
from hue_agent.tunnel_api import TunnelApi
from test_core import FakeIo, payload

class TunnelApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.journal=Journal(Path(self.temp.name)/'journal')
        self.io=FakeIo();self.controller=Controller(self.io,self.journal,'local')
        self.controller.lamp=Lamp(self.io,poll_interval=0);self.controller.ready=True
        self.api=TunnelApi(self.controller,'x'*64)
    async def asyncTearDown(self):
        await self.api.close();self.journal.close();self.temp.cleanup()
    async def test_tunnel_session_isolated_from_local_sequence_and_deduplicated(self):
        self.assertEqual((await self.controller.execute(payload(commandId='local',sessionId='local',sequence=100)))['status'],'verified')
        data=payload(sessionId=self.api.context.session_id)
        first=await self.api.controller.execute(data)
        count=len(self.io.writes)
        self.assertEqual(first['status'],'verified')
        self.assertEqual(await self.api.controller.execute(data),first)
        self.assertEqual(len(self.io.writes),count)
    async def test_closing_fences_a_command_waiting_for_ble_lock(self):
        await self.controller.lock.acquire()
        task=asyncio.create_task(self.api.controller.execute(payload(sessionId=self.api.context.session_id)))
        await asyncio.sleep(0)
        await self.api.close();self.controller.lock.release()
        self.assertEqual((await task)['errorCode'],'STALE_SESSION')
        self.assertEqual(self.io.writes,[])
    async def test_rejects_long_deadline_and_unsafe_sequence_before_writes(self):
        for changes in ({'expiresAt':time.time()+20},{'sequence':2**53}):
            result=await self.api.controller.execute(payload(sessionId=self.api.context.session_id,**changes))
            self.assertEqual(result['errorCode'],'INVALID_COMMAND')
        self.assertEqual(self.io.writes,[])
    async def test_revocation_rotates_session_and_fences_queued_write(self):
        old=self.api.context.session_id
        await self.controller.lock.acquire()
        task=asyncio.create_task(self.api.controller.execute(payload(sessionId=old)))
        await asyncio.sleep(0)
        self.api.revoke()
        self.controller.lock.release()
        self.assertEqual((await task)['errorCode'],'STALE_SESSION')
        self.assertNotEqual(self.api.context.session_id,old)
        self.assertEqual(self.io.writes,[])
    async def test_session_endpoint_requires_bearer(self):
        await self.api.start(0)
        port=self.api.server.sockets[0].getsockname()[1]
        async def get(auth):
            r,w=await asyncio.open_connection('127.0.0.1',port)
            w.write(('GET /v1/session HTTP/1.1\r\nHost: localhost\r\n'+auth+'\r\n').encode());await w.drain()
            raw=await r.read();w.close();await w.wait_closed();return raw
        self.assertIn(b'401 Unauthorized',await get(''))
        response=await get('Authorization: Bearer '+'x'*64+'\r\n')
        self.assertIn(b'200 OK',response)
        self.assertIn(self.api.context.session_id.encode(),response)
    async def test_oversized_or_non_string_command_id_is_not_echoed_on_parse_error(self):
        for bad in (123, 'x'*200):
            result=await self.api.controller.execute(payload(commandId=bad,sessionId=self.api.context.session_id))
            self.assertIsNone(result['commandId'])
            self.assertEqual(result['errorCode'],'INVALID_COMMAND')
