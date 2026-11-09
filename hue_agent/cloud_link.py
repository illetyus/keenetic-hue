"""Optional outbound WSS task; never owns USB or changes local API credentials."""
import asyncio
import contextlib
import json
import random
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

from .events import emit
from .private_files import private_file
from .protocol import Command, ExecutionContext

HEARTBEAT_SECONDS = 30
PONG_SECONDS = 10


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'wss' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != '/agent':
        raise ValueError('INVALID_CLOUD_URL')
    return url


def packet(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 8192:
        raise ValueError('INVALID_MESSAGE')
    data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('INVALID_MESSAGE')))
    if not isinstance(data, dict) or data.get('v') != 1 or type(data['v']) is not int:
        raise ValueError('INVALID_MESSAGE')
    return data


def fields(data, allowed):
    if data.keys() != set(allowed):
        raise ValueError('INVALID_MESSAGE')


class CloudLink:
    def __init__(self, controller, config):
        self.controller = controller
        self.config = config
        self.stopped = asyncio.Event()
        self.socket = None
        self.current = None
        self.tasks = set()
        self.ready_since = None

    async def close(self):
        self.stopped.set()
        if self.current is not None:
            self.current['live'] = False
        if self.socket is not None:
            await self.socket.close()

    async def _send(self, ws, data):
        await ws.send(json.dumps(data, separators=(',', ':'), allow_nan=False))

    async def session(self, ws):
        self.socket = ws
        live = {'live': False}
        self.current = live
        jobs = set()
        heartbeat = None
        pong = asyncio.Event()
        try:
            start = time.monotonic()
            hello = packet(await asyncio.wait_for(ws.recv(), 2))
            fields(hello, ('v','type','sessionId','serverTime'))
            if hello['type'] != 'hello' or not isinstance(hello['sessionId'], str) or not 1 <= len(hello['sessionId']) <= 128:
                raise ValueError('INVALID_HELLO')
            if type(hello['serverTime']) not in (int, float) or not __import__('math').isfinite(hello['serverTime']):
                raise ValueError('CLOCK_SKEW')
            session_id = hello['sessionId']
            await self._send(ws, dict(v=1,type='ready',sessionId=session_id,
                agentSessionId=self.controller.session_id,ready=self.controller.ready,clientTime=hello['serverTime']+time.monotonic()-start))
            ack = packet(await asyncio.wait_for(ws.recv(), 2))
            fields(ack, ('v','type','sessionId','serverTime'))
            if ack['type'] != 'ready_ack' or ack['sessionId'] != session_id or time.monotonic()-start > 2:
                raise ValueError('INVALID_HANDSHAKE')
            if type(ack['serverTime']) not in (int,float) or not __import__('math').isfinite(ack['serverTime']):raise ValueError('INVALID_HANDSHAKE')
            live['live'] = True
            self.ready_since = time.monotonic()
            acknowledged=time.monotonic()
            # Conservative server clock: count the entire handshake as elapsed
            # after the acknowledgement; transit can only shorten command TTL.
            server_base=ack['serverTime']+acknowledged-start
            server_clock=lambda:server_base+time.monotonic()-acknowledged
            context = ExecutionContext(session_id, lambda: live['live'] and not self.stopped.is_set(),clock=server_clock)
            async def heartbeats():
                while True:
                    await asyncio.sleep(HEARTBEAT_SECONDS)
                    pong.clear()
                    await ws.send('ping')
                    try:
                        await asyncio.wait_for(pong.wait(), PONG_SECONDS)
                    except TimeoutError:
                        live['live'] = False
                        await ws.close()
                        return
            heartbeat = asyncio.create_task(heartbeats())

            async def execute(command):
                result = await self.controller.execute(command, context=context)
                if context.is_current():
                    await self._send(ws, dict(v=1,type='result',sessionId=session_id,
                        commandId=command['commandId'],result=result))

            def finished(task):
                jobs.discard(task)
                if not task.cancelled():
                    task.exception()  # Consume send failures; no credentials/payload in logs.
            while not self.stopped.is_set():
                raw = await ws.recv()
                if raw == 'pong':
                    pong.set()
                    continue
                data = packet(raw)
                fields(data, ('v','type','sessionId','commandId','command'))
                if data['type'] != 'command' or data['sessionId'] != session_id:
                    raise ValueError('INVALID_SESSION')
                command = data['command']
                if len(json.dumps(command, separators=(',', ':')).encode()) > 4096:
                    raise ValueError('INVALID_SIZE')
                parsed = Command.parse(command, session_id, allow_expired=True,clock=server_clock)
                if parsed.command_id != data['commandId'] or not 1 <= parsed.sequence <= 2**53-1 or parsed.expires_at > server_clock()+6:
                    raise ValueError('INVALID_COMMAND')
                if len(jobs) >= 16:
                    await self._send(ws, dict(v=1,type='result',sessionId=session_id,commandId=parsed.command_id,
                        result=dict(commandId=parsed.command_id,status='error',errorCode='BUSY')))
                    continue
                task = asyncio.create_task(execute(command))
                jobs.add(task)
                task.add_done_callback(finished)
        finally:
            live['live'] = False
            if heartbeat is not None:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat
            if jobs:
                # Give bounded GATT operations a chance to observe invalidation.
                _, remaining = await asyncio.wait(jobs, timeout=6)
                for task in remaining:
                    task.cancel()
                if remaining:
                    await asyncio.gather(*remaining, return_exceptions=True)
            self.socket = None
            self.current = None

    async def run(self):
        delay = 1
        while not self.stopped.is_set():
            try:
                url = validate_url(self.config['url'])
                path = private_file(self.config['token_file'])
                token = path.read_text(encoding='utf-8').strip()
                if len(token) < 32 or len(token) > 256 or not token.isascii() or any(c.isspace() for c in token):
                    raise ValueError('INVALID_CLOUD_TOKEN')
                # Lazy import: local-only installations need no websocket package.
                from websockets.asyncio.client import connect
                self.ready_since = None
                async with connect(url, additional_headers={'Authorization':'Bearer '+token},
                                   ssl=ssl.create_default_context(), proxy=None,
                                   max_size=8192,max_queue=16,ping_interval=None,
                                   open_timeout=10,close_timeout=3) as ws:
                    emit('cloud_connected')
                    await self.session(ws)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # A successful Upgrade alone is not a healthy application session.
                if self.ready_since is not None and time.monotonic()-self.ready_since >= 30:
                    delay = 1
                status = getattr(getattr(error, 'response', None), 'status_code', None)
                retry = 60 if status in (401,403) else random.uniform(0.1, delay)
                emit('cloud_retry',errorType=type(error).__name__,retrySeconds=round(retry,2))
                try:
                    await asyncio.wait_for(self.stopped.wait(), retry)
                except TimeoutError:
                    pass
                delay = min(30, delay*2)
