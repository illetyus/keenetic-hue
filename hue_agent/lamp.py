import asyncio
import time

from .protocol import observed_state

POWER = '932C32BD-0002-47A2-835A-A8D455B859DD'
BRIGHTNESS = '932C32BD-0003-47A2-835A-A8D455B859DD'
MODEL = '00002A24-0000-1000-8000-00805F9B34FB'
MANUFACTURER = '00002A29-0000-1000-8000-00805F9B34FB'


def remaining(deadline):
    value = deadline-time.monotonic()
    if value <= 0:
        raise TimeoutError('DEADLINE_EXCEEDED')
    return value


async def bounded(factory, deadline, limit=5):
    timeout = min(limit, remaining(deadline))
    return await asyncio.wait_for(factory(), timeout)


class Lamp:
    def __init__(self, io, poll_interval=0.15):
        self.io = io
        self.poll_interval = poll_interval

    async def identify(self):
        for uuid, expected in ((MODEL, b'LWA017'), (MANUFACTURER, b'Signify Netherlands B.V.')):
            actual = await asyncio.wait_for(self.io.read(uuid), 5)
            if actual.rstrip(b'\x00') != expected:
                raise ValueError('UNEXPECTED_DEVICE')

    async def read_state(self, deadline):
        power = await bounded(lambda: self.io.read(POWER), deadline)
        brightness = await bounded(lambda: self.io.read(BRIGHTNESS), deadline)
        if power not in (b'\x00', b'\x01') or len(brightness) != 1 or brightness[0] > 254:
            raise ValueError('MALFORMED_STATE')
        return observed_state(bool(power[0]), brightness[0])

    async def _write(self, uuid, data, deadline, guard=None, mark_write_started=None):
        if uuid not in (POWER, BRIGHTNESS) or len(data) != 1:
            raise ValueError('INVALID_CONTROL')
        # Deadline is checked immediately before each write, including queued commands.
        def write():
            if guard is not None:
                guard()
            if mark_write_started is not None:
                mark_write_started()
            return self.io.write(uuid, data)
        await bounded(write, deadline)
        until = min(deadline, time.monotonic()+3)
        while True:
            remaining(until)
            actual = await bounded(lambda: self.io.read(uuid), until)
            if actual == data:
                return
            await asyncio.sleep(min(self.poll_interval, remaining(until)))

    async def set_raw(self, *, on=None, brightness_raw=None, deadline, guard=None, mark_write_started=None):
        initial = await self.read_state(deadline)
        if brightness_raw is not None:
            if type(brightness_raw) is not int or not 1 <= brightness_raw <= 254:
                raise ValueError('INVALID_BRIGHTNESS')
            await self._write(BRIGHTNESS, bytes([brightness_raw]), deadline, guard, mark_write_started)
        if on is not None:
            if type(on) is not bool:
                raise ValueError('INVALID_POWER')
            await self._write(POWER, bytes([int(on)]), deadline, guard, mark_write_started)
        # Power transitions also temporarily change brightness readback. Require the
        # combined final state, preserving remembered brightness on pure power writes.
        expected_raw = brightness_raw if brightness_raw is not None else initial['brightnessRaw']
        until = min(deadline, time.monotonic()+3)
        while True:
            state = await self.read_state(deadline)
            if (on is None or state['on'] == on) and state['brightnessRaw'] == expected_raw:
                return state
            if time.monotonic() >= until:
                raise RuntimeError('STATE_CHANGED_DURING_COMMAND')
            await asyncio.sleep(min(self.poll_interval, until-time.monotonic()))
