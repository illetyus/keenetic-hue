import asyncio
import tempfile
import unittest
from pathlib import Path

from hue_agent.agent import Controller
from hue_agent.journal import Journal
from hue_agent.lamp import Lamp, POWER, BRIGHTNESS
from hue_agent.protocol import ExecutionContext
from test_core import FakeIo, payload


class CloudContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.temp.name)/'journal.sqlite')
        self.io = FakeIo()
        self.agent = Controller(self.io, self.journal, session_id='session1')
        self.agent.lamp = Lamp(self.io, poll_interval=0)
        self.agent.ready = True
        self.current = True
        self.context = ExecutionContext('cloud1', lambda: self.current)

    async def asyncTearDown(self):
        self.journal.close()
        self.temp.cleanup()

    async def cloud(self, **changes):
        return await self.agent.execute(payload(sessionId='cloud1', **changes), context=self.context)

    async def test_local_and_cloud_sequences_are_isolated(self):
        self.assertEqual((await self.agent.execute(payload(commandId='local')))['status'], 'verified')
        self.assertEqual((await self.cloud(commandId='cloud'))['status'], 'verified')
        self.assertEqual(self.agent.session_id, 'session1')

    async def test_replaced_context_waiting_for_lock_does_not_write(self):
        await self.agent.lock.acquire()
        task = asyncio.create_task(self.cloud())
        await asyncio.sleep(0)
        self.current = False
        self.agent.lock.release()
        self.assertEqual((await task)['errorCode'], 'STALE_SESSION')
        self.assertEqual(self.io.writes, [])

    async def test_replaced_context_between_brightness_and_power_stops_second_write(self):
        original = self.io.read
        async def read(uuid):
            result = await original(uuid)
            if self.io.writes and uuid == BRIGHTNESS and result == bytes([102]):
                self.current = False
            return result
        self.io.read = read
        result = await self.cloud()
        self.assertEqual(result['errorCode'], 'OUTCOME_UNKNOWN')
        self.assertEqual([uuid for uuid, _ in self.io.writes], [BRIGHTNESS])
        self.assertTrue(self.agent.ready)

    async def test_invalidation_during_last_write_is_unknown(self):
        original = self.io.write
        async def write(uuid, data):
            await original(uuid, data)
            if uuid == POWER:
                self.current = False
        self.io.write = write
        self.assertEqual((await self.cloud())['errorCode'], 'OUTCOME_UNKNOWN')
        stored = self.journal.db.execute('SELECT result FROM commands').fetchone()[0]
        self.assertNotIn('verified', stored)
        self.assertIn('OUTCOME_UNKNOWN', stored)

    async def test_invalidation_during_final_readback_is_unknown(self):
        original = self.agent.lamp.set_raw
        async def set_raw(**kwargs):
            result = await original(**kwargs)
            self.current = False
            return result
        self.agent.lamp.set_raw = set_raw
        self.assertEqual((await self.cloud())['errorCode'], 'OUTCOME_UNKNOWN')
        self.assertTrue(self.agent.ready)

    async def test_local_api_survives_cloud_context_failure(self):
        self.current = False
        self.assertEqual((await self.cloud())['errorCode'], 'STALE_SESSION')
        self.assertEqual((await self.agent.query())['status'], 'verified')
        self.assertEqual((await self.agent.execute(payload(commandId='local')))['status'], 'verified')

    async def test_invalidated_context_cannot_retrieve_cached_verified_result(self):
        data = payload(sessionId='cloud1')
        self.assertEqual((await self.agent.execute(data, context=self.context))['status'], 'verified')
        self.current = False
        self.assertEqual((await self.agent.execute(data, context=self.context))['errorCode'], 'STALE_SESSION')
