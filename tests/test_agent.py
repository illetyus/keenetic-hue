import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from hue_agent.agent import Controller
from hue_agent.local_api import LocalApi
from hue_agent.bumble_io import BleIo
from hue_agent.protocol import Command
from test_core import FakeIo, payload
from hue_agent.lamp import Lamp
from hue_agent.journal import Journal


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.io = FakeIo()
        self.journal = Journal(Path(self.temp.name)/'commands.sqlite')
        self.agent = Controller(self.io, self.journal, session_id='session1')
        self.agent.lamp = Lamp(self.io, poll_interval=0)
        self.agent.ready = True

    async def asyncTearDown(self):
        self.journal.close()
        self.temp.cleanup()

    async def test_duplicate_no_second_write_and_conflict(self):
        data = payload()
        first = await self.agent.execute(data)
        self.assertEqual(first['status'], 'verified')
        writes = len(self.io.writes)
        self.assertEqual(await self.agent.execute(data), first)
        self.assertEqual(len(self.io.writes), writes)
        result = await self.agent.execute(dict(data, brightness=70))
        self.assertEqual(result['errorCode'], 'COMMAND_ID_CONFLICT')

    async def test_queued_expiry_never_writes(self):
        await self.agent.lock.acquire()
        try:
            data = payload(expiresAt=time.time()+0.03)
            result = await self.agent.execute(data)
            self.assertEqual(result['status'], 'expired')
            self.assertEqual(self.io.writes, [])
        finally:
            self.agent.lock.release()

    async def test_offline_has_no_fabricated_state(self):
        self.agent.ready = False
        result = await self.agent.execute(payload())
        self.assertEqual(result['status'], 'offline')
        self.assertNotIn('on', result)

    async def test_monitor_recovers_after_connection_drop(self):
        self.io.disconnected = True
        self.agent.ready = False
        self.io.connects = 0
        async def connect():
            self.io.connects += 1
            self.io.disconnected = False
        self.io.connect = connect
        task = asyncio.create_task(self.agent.monitor())
        try:
            for _ in range(20):
                if self.agent.ready:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(self.agent.ready)
            self.io.disconnected = True
            for _ in range(150):
                if self.io.connects == 2:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(self.io.connects, 2)
            self.assertTrue(self.agent.ready)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_replay_after_expiry_returns_prior_result(self):
        data = payload(expiresAt=time.time()+5)
        first = await self.agent.execute(data)
        self.assertEqual(first['status'], 'verified')
        with patch('hue_agent.agent.time.time', return_value=data['expiresAt']+1):
            self.assertEqual(await self.agent.execute(data), first)

    async def test_internal_cancellation_records_unknown_not_verified(self):
        async def interrupted(*args, **kwargs):
            raise asyncio.CancelledError('link lost')
        self.agent.lamp.set_raw = interrupted
        result = await self.agent.execute(payload())
        self.assertEqual(result['errorCode'], 'OUTCOME_UNKNOWN')
        self.assertFalse(self.agent.ready)

    async def test_ble_io_rejects_unencrypted_and_unknown_controls(self):
        io = BleIo({})
        with self.assertRaises(ConnectionError):
            await io.read('unknown')
        from types import SimpleNamespace
        io.connection = SimpleNamespace(is_encrypted=True)
        io.disconnected = False
        io.characteristics['unknown'] = object()
        with self.assertRaises(ValueError):
            await io.write('unknown', b'\x01')

    async def test_shutdown_drains_partial_requests(self):
        api = LocalApi(self.agent, 'test-token')
        server = await api.start(0)
        reader, writer = await asyncio.open_connection('127.0.0.1', server.sockets[0].getsockname()[1])
        writer.write(b'POST /v1/commands HTTP/1.1\r\nAuthorization: Bearer test-token\r\nContent-Length: 100\r\n\r\n{')
        await writer.drain()
        await asyncio.sleep(0.02)
        await api.close()
        self.assertFalse(api.tasks)
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b'')
        writer.close()
        await writer.wait_closed()

    async def test_shutdown_cancels_inflight_and_queued_clients_before_journal_close(self):
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        self.agent.lamp.set_raw = blocked
        api = LocalApi(self.agent, 'test-token')
        server = await api.start(0)
        port = server.sockets[0].getsockname()[1]
        body = json.dumps(payload()).encode()
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        writer.write(b'POST /v1/commands HTTP/1.1\r\nAuthorization: Bearer test-token\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
        await writer.drain()
        await asyncio.wait_for(entered.wait(), 1)
        reader2, writer2 = await asyncio.open_connection('127.0.0.1', port)
        writer2.write(b'GET /v1/state HTTP/1.1\r\nAuthorization: Bearer test-token\r\n\r\n')
        await writer2.drain()
        await asyncio.sleep(0.02)
        await api.close()
        self.assertFalse(api.tasks)
        self.assertFalse(self.agent.lock.locked())
        saved = self.journal.db.execute('SELECT result FROM commands WHERE id=?',('cmd1',)).fetchone()
        self.assertEqual(json.loads(saved[0])['errorCode'], 'OUTCOME_UNKNOWN')
        self.assertEqual(await reader.read(), b'')
        self.assertEqual(await reader2.read(), b'')
        for client in (writer, writer2):
            client.close()
            await client.wait_closed()

    async def test_api_auth_fresh_query_and_size_limit(self):
        api = LocalApi(self.agent, 'test-token')
        server = await api.start(0)
        port = server.sockets[0].getsockname()[1]
        async def request(text):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(text.encode())
            await writer.drain()
            raw = await reader.read()
            writer.close()
            await writer.wait_closed()
            return raw.decode()
        try:
            response = await request('GET /v1/state HTTP/1.1\r\nHost: localhost\r\n\r\n')
            self.assertIn('401 Unauthorized', response)
            response = await request('GET /v1/state HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer test-token\r\n\r\n')
            body = json.loads(response.split('\r\n\r\n')[1])
            self.assertEqual(body['sessionId'], 'session1')
            self.assertEqual(body['brightnessRaw'], 254)
            response = await request('POST /v1/commands HTTP/1.1\r\nAuthorization: Bearer test-token\r\nContent-Length: 99999\r\n\r\n')
            self.assertIn('413 Payload Too Large', response)
        finally:
            server.close()
            await server.wait_closed()

    async def test_inflight_retry_waits_for_result_instead_of_returning_outcome_unknown(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        original = self.agent.lamp.set_raw
        async def slow_set_raw(**kwargs):
            entered.set()
            await release.wait()
            return await original(**kwargs)
        self.agent.lamp.set_raw = slow_set_raw
        data = payload(expiresAt=time.time()+5)
        first = asyncio.create_task(self.agent.execute(dict(data)))
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(self.agent.execute(dict(data)))
        await asyncio.sleep(0.02)
        self.assertFalse(second.done())
        release.set()
        r1, r2 = await asyncio.gather(first, second)
        self.assertEqual(r1['status'], 'verified')
        self.assertEqual(r2, r1)
        self.assertEqual(len(self.io.writes), 2)

    async def test_crash_left_pending_row_still_returns_outcome_unknown(self):
        data = payload()
        self.journal.reserve(Command.parse(data, 'session1'))
        result = await self.agent.execute(data)
        self.assertEqual(result['errorCode'], 'OUTCOME_UNKNOWN')
        self.assertEqual(self.io.writes, [])

    async def test_offline_reconnect_returns_ble_offline_without_waiting_on_lock(self):
        self.agent.ready = False
        self.io.disconnected = True
        connecting = asyncio.Event()
        release = asyncio.Event()
        async def slow_connect():
            connecting.set()
            await release.wait()
            self.io.disconnected = False
        self.io.connect = slow_connect
        monitor = asyncio.create_task(self.agent.monitor())
        try:
            await asyncio.wait_for(connecting.wait(), 1)
            result = await asyncio.wait_for(self.agent.execute(payload(expiresAt=time.time()+0.5)), 0.2)
            self.assertEqual(result['status'], 'offline')
            self.assertEqual(result['errorCode'], 'BLE_OFFLINE')
        finally:
            release.set()
            monitor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await monitor

    async def test_monitor_probe_detects_silent_drop(self):
        agent = Controller(self.io, self.journal, session_id='session1', probe_interval=0.02)
        agent.lamp = Lamp(self.io, poll_interval=0)
        self.io.disconnected = True
        connects = 0
        fail_read = False
        async def connect():
            nonlocal connects
            connects += 1
            self.io.disconnected = False
        orig_read = self.io.read
        async def read(uuid):
            if fail_read:
                raise ConnectionError('usb dropped')
            return await orig_read(uuid)
        self.io.connect = connect
        self.io.read = read
        monitor = asyncio.create_task(agent.monitor())
        try:
            for _ in range(50):
                if agent.ready:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(agent.ready)
            fail_read = True
            for _ in range(100):
                if not agent.ready:
                    break
                await asyncio.sleep(0.01)
            self.assertFalse(agent.ready)
            fail_read = False
            for _ in range(150):
                if connects >= 2 and agent.ready:
                    break
                await asyncio.sleep(0.01)
            self.assertGreaterEqual(connects, 2)
            self.assertTrue(agent.ready)
        finally:
            monitor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await monitor

    async def test_journal_finish_error_returns_structured_result(self):
        with patch.object(self.journal, 'finish', side_effect=sqlite3.OperationalError('disk full')):
            result = await self.agent.execute(payload())
            self.assertEqual(result['status'], 'verified')
            self.assertTrue(self.agent.ready)
            async def fail_set(**kwargs):
                raise TimeoutError('DEADLINE_EXCEEDED')
            self.agent.lamp.set_raw = fail_set
            failed = await self.agent.execute(payload(commandId='cmd2', sequence=2))
            self.assertEqual(failed['errorCode'], 'OUTCOME_UNKNOWN')

    async def test_ble_io_close_preserves_external_cancellation_and_closes_stack(self):
        io = BleIo({})
        io.disconnected = False
        aclose_called = False
        class FakeConnection:
            async def disconnect(self):
                await asyncio.Event().wait()
        class FakeStack:
            async def aclose(self):
                nonlocal aclose_called
                aclose_called = True
        io.connection = FakeConnection()
        io.stack = FakeStack()
        task = asyncio.create_task(io.close())
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(aclose_called)
        self.assertIsNone(io.stack)
        self.assertIsNone(io.connection)
        self.assertTrue(io.disconnected)

    async def test_ble_io_close_absorbs_stack_aclose_timeout(self):
        io = BleIo({})
        class HangingStack:
            async def aclose(self):
                raise TimeoutError()
        io.stack = HangingStack()
        await io.close()
        self.assertIsNone(io.stack)
        self.assertTrue(io.disconnected)

    async def test_local_api_empty_or_missing_content_length_is_not_413(self):
        api = LocalApi(self.agent, 'test-token')
        server = await api.start(0)
        port = server.sockets[0].getsockname()[1]
        async def request(text):
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            writer.write(text.encode())
            await writer.drain()
            raw = await reader.read()
            writer.close()
            await writer.wait_closed()
            return raw.decode()
        try:
            missing = await request('POST /v1/commands HTTP/1.1\r\nAuthorization: Bearer test-token\r\n\r\n')
            self.assertIn('411 Length Required', missing)
            empty = await request('POST /v1/commands HTTP/1.1\r\nAuthorization: Bearer test-token\r\nContent-Length: 0\r\n\r\n')
            self.assertIn('400 Bad Request', empty)
            self.assertIn('INVALID_SIZE', empty)
            oversized = await request('POST /v1/commands HTTP/1.1\r\nAuthorization: Bearer test-token\r\nContent-Length: 99999\r\n\r\n')
            self.assertIn('413 Payload Too Large', oversized)
        finally:
            await api.close()
