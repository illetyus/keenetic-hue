import asyncio
import json
import random
import time
import uuid
from dataclasses import replace

from .lamp import Lamp
from .protocol import Command, ProtocolError
from .events import emit


class Controller:
    def __init__(self, io, journal, session_id=None, probe_interval=30):
        self.io = io
        self.journal = journal
        self.session_id = session_id or str(uuid.uuid4())
        self.lock = asyncio.Lock()
        self.lamp = Lamp(io)
        self.ready = False
        self.waiters = 0
        self.probe_interval = probe_interval

    def _finish_journal(self, command, result):
        try:
            self.journal.finish(command, result)
        except Exception as error:
            emit('journal_finish_failed', errorType=type(error).__name__)

    async def monitor(self):
        delay = 1
        last_ok = time.monotonic()
        failures = 0
        last_error = None
        while True:
            try:
                if not self.ready or self.io.disconnected:
                    async with self.lock:
                        self.ready = False
                    await self.io.connect()
                    await self.lamp.identify()
                    await self.lamp.read_state(time.monotonic()+5)
                    async with self.lock:
                        if self.io.disconnected:
                            raise ConnectionError('BLE_DISCONNECTED')
                        self.ready = True
                    prior_failures = failures
                    delay = 1
                    failures = 0
                    last_error = None
                    last_ok = time.monotonic()
                    emit('ble_ready', **({'recoveredAfter': prior_failures} if prior_failures else {}))
                elif time.monotonic()-last_ok >= self.probe_interval and not self.lock.locked():
                    async with self.lock:
                        if self.ready and not self.io.disconnected:
                            await self.lamp.read_state(time.monotonic()+5)
                            last_ok = time.monotonic()
                await asyncio.sleep(min(1, self.probe_interval))
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                self.ready = False
                await asyncio.sleep(delay)
                delay = min(30, delay*2)
            except Exception as error:
                self.ready = False
                error_name = type(error).__name__
                failures += 1
                if failures <= 6 or error_name != last_error or failures % 80 == 0:
                    emit('ble_retry', errorType=error_name, retrySeconds=delay, **({'attempts': failures} if failures > 6 else {}))
                last_error = error_name
                await asyncio.sleep(delay+random.uniform(0, 0.2))
                delay = min(30, delay*2)

    async def query(self):
        if not self.ready:
            return {'status':'offline', 'sessionId':self.session_id, 'errorCode':'BLE_OFFLINE'}
        try:
            async with asyncio.timeout(5):
                async with self.lock:
                    if not self.ready:
                        raise ConnectionError('BLE_OFFLINE')
                    result = await self.lamp.read_state(time.monotonic()+5)
                    return dict(result, status='verified', sessionId=self.session_id)
        except (Exception, asyncio.CancelledError) as error:
            if isinstance(error, asyncio.CancelledError) and asyncio.current_task().cancelling():
                raise
            self.ready = False
            return {'status':'offline', 'sessionId':self.session_id, 'errorCode':'READ_FAILED'}

    async def execute(self, data, *, context=None):
        if context is not None:
            # Per-operation evidence must not leak across concurrent/retried requests.
            context = replace(context, write_started=False)
        command = None
        acquired = False
        reserved = False
        self.waiters += 1
        try:
            if self.waiters > 16:
                raise ProtocolError('BUSY')
            if context is not None:
                context.require_current()
            clock=context.clock if context else time.time
            command = Command.parse(data, context.session_id if context else self.session_id, allow_expired=True,clock=clock)
            cached = self.journal.lookup(command, include_pending=False)
            if cached is not None:
                return cached
            remaining = command.expires_at-clock()
            if remaining <= 0:
                raise ProtocolError('EXPIRED')
            if not self.ready:
                return {'commandId':command.command_id, 'status':'offline', 'errorCode':'BLE_OFFLINE'}
            deadline = time.monotonic()+remaining
            await asyncio.wait_for(self.lock.acquire(), remaining)
            acquired = True
            if context is not None:
                context.require_current()
            cached = self.journal.lookup(command)
            if cached is not None:
                return cached
            if time.monotonic() >= deadline:
                raise ProtocolError('EXPIRED')
            if not self.ready:
                return {'commandId':command.command_id, 'status':'offline', 'errorCode':'BLE_OFFLINE'}
            cached = self.journal.reserve(command)
            if cached is not None:
                return cached
            reserved = True
            if command.operation == 'query':
                state = await self.lamp.read_state(deadline)
            else:
                options = {} if context is None else {
                    'guard': context.require_current,
                    'mark_write_started': context.mark_write_started,
                }
                state = await self.lamp.set_raw(on=command.power, brightness_raw=command.brightness_raw, deadline=deadline, **options)
            if context is not None:
                context.require_current()
            result = dict(state, commandId=command.command_id, status='verified')
            if context and context.clock is not time.time:result['observedAt']=context.clock()
            self._finish_journal(command, result)
            return result
        except ProtocolError as error:
            code = 'OUTCOME_UNKNOWN' if context and context.write_started else error.code
            result = {'commandId':getattr(command, 'command_id', None),
                      'status':'expired' if code == 'EXPIRED' else 'error', 'errorCode':code}
            if reserved:
                self._finish_journal(command, result)
            return result
        except TimeoutError:
            result = {'commandId':getattr(command, 'command_id', None),
                      'status':'error' if reserved else 'expired',
                      'errorCode':'OUTCOME_UNKNOWN' if reserved else 'EXPIRED'}
            if reserved:
                self._finish_journal(command, result)
                self.ready = False
            return result
        except (Exception, asyncio.CancelledError) as error:
            result = {'commandId':getattr(command, 'command_id', None), 'status':'error', 'errorCode':'OUTCOME_UNKNOWN'}
            if reserved:
                self._finish_journal(command, result)
            self.ready = False
            if isinstance(error, asyncio.CancelledError) and asyncio.current_task().cancelling():
                raise
            return result
        finally:
            if acquired:
                self.lock.release()
            self.waiters -= 1
