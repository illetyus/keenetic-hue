import hashlib
import json
import math
import time
from dataclasses import dataclass
from typing import Callable

DEVICE_ID = 'hue-lwa017-01'


class ProtocolError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass
class ExecutionContext:
    session_id: str
    is_current: Callable[[], bool]
    write_started: bool = False
    clock: Callable[[],float] = time.time

    def require_current(self):
        if not self.is_current():
            raise ProtocolError('STALE_SESSION')

    def mark_write_started(self):
        self.write_started = True


@dataclass(frozen=True)
class Command:
    payload: dict
    command_id: str
    session_id: str
    sequence: int
    expires_at: float
    operation: str
    power: bool | None
    brightness_raw: int | None
    fingerprint: str

    @classmethod
    def parse(cls, data, session_id, *, allow_expired=False, clock=None):
        current_time=(clock or time.time)()
        required = {'commandId', 'deviceId', 'sessionId', 'sequence', 'expiresAt', 'operation'}
        if not isinstance(data, dict) or not required <= data.keys() or data.keys()-required-{'on', 'brightness'}:
            raise ProtocolError('INVALID_COMMAND')
        for key in ('commandId', 'deviceId', 'sessionId'):
            if not isinstance(data[key], str) or not 1 <= len(data[key]) <= 128:
                raise ProtocolError('INVALID_COMMAND')
        if data['deviceId'] != DEVICE_ID:
            raise ProtocolError('UNKNOWN_DEVICE')
        if data['sessionId'] != session_id:
            raise ProtocolError('STALE_SESSION')
        if type(data['sequence']) is not int or not 0 <= data['sequence'] < 2**63:
            raise ProtocolError('INVALID_SEQUENCE')
        expiry = data['expiresAt']
        if type(expiry) not in (int, float) or (type(expiry) is float and not math.isfinite(expiry)):
            raise ProtocolError('INVALID_EXPIRY')
        if expiry > current_time+60:
            raise ProtocolError('INVALID_EXPIRY')
        if not allow_expired and expiry <= current_time:
            raise ProtocolError('EXPIRED')
        if data['operation'] not in ('query', 'set'):
            raise ProtocolError('INVALID_OPERATION')
        if 'on' in data and type(data['on']) is not bool:
            raise ProtocolError('INVALID_POWER')
        brightness = data.get('brightness')
        if 'brightness' in data and (type(brightness) is not int or not 0 <= brightness <= 100):
            raise ProtocolError('INVALID_BRIGHTNESS')
        if data['operation'] == 'query' and ('on' in data or 'brightness' in data):
            raise ProtocolError('INVALID_QUERY')
        if data['operation'] == 'set' and not {'on', 'brightness'} & data.keys():
            raise ProtocolError('EMPTY_SET')
        power = data.get('on')
        raw = None
        if brightness is not None:
            if power is not None and power != (brightness > 0):
                raise ProtocolError('CONFLICTING_STATE')
            power = brightness > 0
            if brightness > 0:
                raw = max(1, min(254, round(brightness*254/100)))
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        return cls(dict(data), data['commandId'], session_id, data['sequence'], expiry,
                   data['operation'], power, raw, fingerprint)


def observed_state(on, raw):
    return {'deviceId': DEVICE_ID, 'on': on, 'brightnessRaw': raw,
            'brightness': round(raw*100/254) if on else 0, 'observedAt': time.time()}
