import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from hue_agent.protocol import Command, ProtocolError, observed_state
from hue_agent.lamp import Lamp, POWER, BRIGHTNESS, MODEL, MANUFACTURER
from hue_agent.journal import Journal


def payload(**updates):
    value = dict(commandId='cmd1', deviceId='hue-lwa017-01', sessionId='session1',
                 sequence=1, expiresAt=time.time()+5, operation='set', brightness=40)
    value.update(updates)
    return value


class ProtocolTests(unittest.TestCase):
    def test_mapping_and_invalid_values(self):
        for percent, raw in ((0, None), (1, 3), (40, 102), (70, 178), (100, 254)):
            command = Command.parse(payload(brightness=percent), 'session1')
            self.assertEqual(command.brightness_raw, raw)
            self.assertEqual(command.power, percent > 0)
        for updates in (dict(brightness=True), dict(brightness=1.5), dict(brightness=-1),
                        dict(brightness=101), dict(on=1), dict(sequence=True),
                        dict(expiresAt=float('nan')), dict(unknown=1),
                        dict(expiresAt=10**1000), dict(sequence=2**64),
                        dict(on=False, brightness=40), dict(on=True, brightness=0)):
            with self.subTest(updates=updates), self.assertRaises(ProtocolError):
                Command.parse(payload(**updates), 'session1')

    def test_expired_wrong_device_and_session(self):
        for updates in (dict(expiresAt=time.time()-1), dict(deviceId='other'),
                        dict(sessionId='old')):
            with self.assertRaises(ProtocolError):
                Command.parse(payload(**updates), 'session1')

    def test_off_preserves_raw_but_reports_zero(self):
        self.assertEqual(observed_state(False, 178)['brightness'], 0)
        self.assertEqual(observed_state(False, 178)['brightnessRaw'], 178)


class JournalTests(unittest.TestCase):
    def test_replay_conflict_order_and_crash_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'journal.sqlite'
            command = Command.parse(payload(), 'session1')
            journal = Journal(path)
            self.assertIsNone(journal.reserve(command))
            journal.close()
            journal = Journal(path)
            self.assertEqual(journal.reserve(command)['errorCode'], 'OUTCOME_UNKNOWN')
            other = Command.parse(payload(brightness=70), 'session1')
            with self.assertRaises(ProtocolError):
                journal.reserve(other)
            stale = Command.parse(payload(commandId='cmd2', sequence=0), 'session1')
            with self.assertRaises(ProtocolError):
                journal.reserve(stale)
            journal.finish(command, {'status':'verified', 'commandId':'cmd1'})
            self.assertEqual(journal.reserve(command)['status'], 'verified')
            journal.close()


class FakeIo:
    def __init__(self):
        self.values = {POWER:b'\x01', BRIGHTNESS:b'\xfe', MODEL:b'LWA017',
                       MANUFACTURER:b'Signify Netherlands B.V.'}
        self.pending = None
        self.writes = []
        self.bad = False
    async def read(self, uuid):
        if self.pending and uuid == self.pending[0]:
            self.values[uuid] = self.pending[1]
            self.pending = None
            return b'\xae'
        return self.values[uuid]
    async def write(self, uuid, data):
        self.writes.append((uuid, data))
        if not self.bad:
            self.pending = (uuid, data)


class LampTests(unittest.IsolatedAsyncioTestCase):
    async def test_power_transition_settles_combined_brightness(self):
        class FadingIo(FakeIo):
            async def write(self, uuid, data):
                await super().write(uuid, data)
                if uuid == POWER:
                    self.power_fade = True
            async def read(self, uuid):
                if uuid == BRIGHTNESS and getattr(self, 'power_fade', False):
                    self.power_fade = False
                    return b'\x01'
                return await super().read(uuid)
        result = await Lamp(FadingIo(), poll_interval=0).set_raw(
            on=True, brightness_raw=3, deadline=time.monotonic()+1)
        self.assertEqual(result['brightnessRaw'], 3)

    async def test_off_waits_for_preserved_brightness_after_fade(self):
        class OffFadeIo(FakeIo):
            async def write(self, uuid, data):
                await super().write(uuid, data)
                if uuid == POWER:
                    self.off_fade = True
            async def read(self, uuid):
                if uuid == BRIGHTNESS and getattr(self, 'off_fade', False):
                    self.off_fade = False
                    return b'\x8b'
                return await super().read(uuid)
        result = await Lamp(OffFadeIo(), poll_interval=0).set_raw(
            on=False, deadline=time.monotonic()+1)
        self.assertEqual(result['brightnessRaw'], 254)

    async def test_final_state_must_still_match_all_requested_fields(self):
        class RacingIo(FakeIo):
            async def write(self, uuid, data):
                await super().write(uuid, data)
                if uuid == POWER:
                    self.values[BRIGHTNESS] = b'\xfe'
        with self.assertRaises((RuntimeError, TimeoutError)) as failure:
            await Lamp(RacingIo(), poll_interval=0).set_raw(
                on=True, brightness_raw=102, deadline=time.monotonic()+1)
        self.assertIn(str(failure.exception), ('STATE_CHANGED_DURING_COMMAND', 'DEADLINE_EXCEEDED', ''))

    async def test_transition_and_protected_identity(self):
        io = FakeIo()
        lamp = Lamp(io, poll_interval=0)
        await lamp.identify()
        state = await lamp.set_raw(on=True, brightness_raw=102, deadline=time.monotonic()+2)
        self.assertEqual(state['brightnessRaw'], 102)
        self.assertTrue(state['on'])
        io.values[MODEL] = b'wrong'
        with self.assertRaises(ValueError):
            await lamp.identify()

    async def test_deadline_no_stale_write_and_bad_readback(self):
        io = FakeIo()
        lamp = Lamp(io, poll_interval=0)
        with self.assertRaises(TimeoutError):
            await lamp.set_raw(on=False, deadline=time.monotonic()-1)
        self.assertEqual(io.writes, [])
        io.bad = True
        with self.assertRaises(TimeoutError):
            await lamp.set_raw(brightness_raw=102, deadline=time.monotonic()+0.03)

    async def test_malformed_state(self):
        io = FakeIo()
        io.values[POWER] = b'\x02'
        with self.assertRaises(ValueError):
            await Lamp(io).read_state(time.monotonic()+1)


if __name__ == '__main__':
    unittest.main()
