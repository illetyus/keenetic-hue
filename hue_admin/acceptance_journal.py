"""Root-private durable acceptance record; caller holds CLI admission throughout.

The original boot/start tokens are retained during recovery. Never signals a
PID: ambiguous same-boot processes or group inspection block recovery instead.
Ancestors of the configured root must be trusted, as for release maintenance.
"""
import json
import os
from pathlib import Path
import re
import secrets
import stat

from .acceptance_recovery import validate_snapshot
from .acceptance_watchdog import _live_group
from .manifest import unique_object

NAME = 'acceptance-current.json'
LIMIT = 16384


def boot_id():
    value = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    if not re.fullmatch('[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}', value):
        raise ValueError('ACCEPTANCE_BOOT_ID')
    return value


def process_token(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    if fields[0] == 'Z':
        return None
    return {'pid': pid, 'startTicks': int(fields[19]), 'pgid': int(fields[2])}


def _valid_token(token, pid):
    return (isinstance(token, dict) and set(token) == {'pid', 'startTicks', 'pgid'}
            and token['pid'] == pid
            and all(type(v) is int and v > 0 for v in token.values()))


class AcceptanceJournal:
    def __init__(self, root):
        self.root = Path(root)

    def _directory(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root = os.open(self.root, flags)
        try:
            info = os.fstat(root)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('ACCEPTANCE_ROOT')
            directory = os.open('state', flags, dir_fd=root)
        finally:
            os.close(root)
        info = os.fstat(directory)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(directory)
            raise ValueError('ACCEPTANCE_DIRECTORY')
        return directory

    def _read(self, directory):
        fd = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > LIMIT):
                raise ValueError('ACCEPTANCE_FILE')
            data = bytearray()
            while len(data) <= LIMIT:
                block = os.read(fd, LIMIT + 1 - len(data))
                if not block:
                    break
                data.extend(block)
            if len(data) > LIMIT:
                raise ValueError('ACCEPTANCE_FILE')
            value = json.loads(data, object_pairs_hook=unique_object)
            if (not isinstance(value, dict) or set(value) != {'schema', 'bootId', 'supervisor', 'worker', 'snapshot'}
                    or type(value['schema']) is not int or value['schema'] != 1
                    or type(value['bootId']) is not str
                    or not re.fullmatch('[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}', value['bootId'])):
                raise ValueError('ACCEPTANCE_ENVELOPE')
            state = validate_snapshot(value['snapshot'])
            if (not _valid_token(value['supervisor'], state['supervisorPid'])
                    or ('workerPid' in state and not _valid_token(value['worker'], state['workerPid']))
                    or ('workerPid' not in state and value['worker'] is not None)):
                raise ValueError('ACCEPTANCE_PROCESS_TOKEN')
            return value
        finally:
            os.close(fd)

    def load(self):
        directory = self._directory()
        try:
            return self._read(directory)['snapshot']
        finally:
            os.close(directory)

    def save(self, snapshot):
        snapshot = validate_snapshot(snapshot)
        directory = self._directory()
        temporary = '.acceptance-' + secrets.token_hex(16)
        created = False
        try:
            try:
                previous = self._read(directory)
            except FileNotFoundError:
                previous = None
            same = previous is not None and previous['snapshot']['operationId'] == snapshot['operationId']
            if previous is not None and not same and previous['snapshot']['phase'] != 'closed':
                raise ValueError('ACCEPTANCE_RECOVERY_REQUIRED')
            if same:
                old = previous['snapshot']
                if any(old[key] != snapshot[key] for key in ('baselineRelease', 'candidateRelease',
                       'baselineFingerprints', 'baselinePhysical', 'supervisorPid')):
                    raise ValueError('ACCEPTANCE_CONTEXT_CHANGED')
                if 'workerPid' in old and old['workerPid'] != snapshot.get('workerPid'):
                    raise ValueError('ACCEPTANCE_CONTEXT_CHANGED')
                value = {**previous, 'snapshot': snapshot}
            else:
                if snapshot['phase'] != 'prepared' or snapshot['supervisorPid'] != os.getpid():
                    raise ValueError('ACCEPTANCE_INITIAL_STATE')
                value = {'schema': 1, 'bootId': boot_id(), 'supervisor': process_token(os.getpid()),
                         'worker': None, 'snapshot': snapshot}
                if not _valid_token(value['supervisor'], os.getpid()):
                    raise ValueError('ACCEPTANCE_SUPERVISOR')
            if 'workerPid' in snapshot and value['worker'] is None:
                token = process_token(snapshot['workerPid'])
                if not _valid_token(token, snapshot['workerPid']) or token['pgid'] != snapshot['workerPid']:
                    raise ValueError('ACCEPTANCE_WORKER')
                value['worker'] = token
            data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')
            if len(data) > LIMIT:
                raise ValueError('ACCEPTANCE_FILE')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            created = True
            try:
                os.fchmod(fd, 0o600)
                remaining = memoryview(data)
                while remaining:
                    count = os.write(fd, remaining)
                    if count <= 0:
                        raise OSError('ACCEPTANCE_WRITE')
                    remaining = remaining[count:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temporary, NAME, src_dir_fd=directory, dst_dir_fd=directory)
            created = False
            os.fsync(directory)
        finally:
            if created:
                os.unlink(temporary, dir_fd=directory)
            os.close(directory)

    def quiescent(self, snapshot):
        directory = self._directory()
        try:
            value = self._read(directory)
        finally:
            os.close(directory)
        if value['snapshot'] != snapshot:
            return False
        if value['bootId'] != boot_id():
            return True  # No original process survives a kernel boot.
        try:
            if process_token(snapshot['supervisorPid']) == value['supervisor']:
                return False
            if value['worker'] is not None and _live_group(value['worker']['pgid']):
                return False
            return True
        except (OSError, ValueError, IndexError):
            return False
