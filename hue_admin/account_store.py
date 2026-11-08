"""Root-only account persistence. Account files are appended, never replaced.

All four databases are locked before planning. Native /etc symlinks keep their
firmware layout. Profiles are durable before writes so an interrupted sequence
can resume without changing either service's numeric identity.
"""
from contextlib import ExitStack
import json
import hashlib
import os
from pathlib import Path
import stat
import re

from .service_accounts import TABLES, plan_accounts, provision_accounts

SYSTEM = Path('/opt/var/lib/keenetic-hue-system')
ACCOUNT_PATHS = dict(zip(TABLES, map(Path, (
    '/etc/passwd', '/etc/group', '/opt/etc/passwd', '/opt/etc/group'))))
LIMIT = 1024 * 1024


def _read(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    data = bytearray()
    while len(data) <= LIMIT:
        chunk = os.read(fd, min(65536, LIMIT + 1 - len(data)))
        if not chunk:
            return data.decode('utf-8')
        data.extend(chunk)
    raise ValueError('ACCOUNT_TABLE_TOO_LARGE')


def ensure_accounts(root):
    if Path(root).absolute() != SYSTEM:
        raise ValueError('ACCOUNT_CANONICAL_ROOT_REQUIRED')
    return _ensure_accounts(root, ACCOUNT_PATHS)


def _ensure_accounts(root, paths):
    """Internal injectable paths exist only for native disposable fixtures."""
    import fcntl
    from .service_manager import private_bytes
    from .restore_transaction import atomic_replace, sync_directory
    root = Path(root)
    if os.geteuid() != 0 or set(paths) != set(TABLES):
        raise ValueError('ACCOUNT_ROOT_REQUIRED')
    state = root / 'state'
    info = state.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o700
            or any(p.is_symlink() for p in state.parents)):
        raise ValueError('ACCOUNT_PROFILE_DIRECTORY')
    profile = state / 'service-identities.json'
    pending = state / 'account-append.json'
    with ExitStack() as stack:
        descriptors, tables, targets = {}, {}, {}
        seen = set()
        for key in sorted(TABLES):
            path = Path(paths[key])
            if path.is_symlink():
                expected = {'/etc/passwd': Path('/tmp/passwd'), '/etc/group': Path('/tmp/group')}
                if str(path) not in expected or path.resolve() != expected[str(path)]:
                    raise ValueError('ACCOUNT_DATABASE_SYMLINK')
                path = expected[str(path)]
            fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            inode = (info.st_dev, info.st_ino)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o022 or info.st_nlink != 1 or inode in seen):
                raise ValueError('ACCOUNT_DATABASE_OWNER')
            seen.add(inode)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            descriptors[key], targets[key] = fd, (path, inode)
            tables[key] = _read(fd)
        if profile.exists() or profile.is_symlink():
            if profile.lstat().st_uid != 0:
                raise ValueError('ACCOUNT_PROFILE_OWNER')
            identities = json.loads(private_bytes(profile, 4096))
        else:
            identities = None

        def save(value):
            atomic_replace(profile, json.dumps(value, sort_keys=True).encode())

        def write_suffix(key, old, suffix):
            fd = descriptors[key]
            path, inode = targets[key]
            current = path.lstat()
            if (current.st_dev, current.st_ino) != inode or _read(fd) != old:
                raise ValueError('ACCOUNT_DATABASE_CHANGED')
            data = suffix.encode('utf-8')
            os.lseek(fd, 0, os.SEEK_END)
            while data:
                count = os.write(fd, data)
                if count <= 0:
                    raise OSError('ACCOUNT_APPEND_FAILED')
                data = data[count:]
            os.fsync(fd)
            after = path.lstat()
            if ((after.st_dev, after.st_ino) != inode
                    or _read(fd) != old + suffix):
                raise ValueError('ACCOUNT_DATABASE_CHANGED')

        def clear_pending():
            pending.unlink()
            sync_directory(state)

        if pending.exists() or pending.is_symlink():
            if identities is None or pending.lstat().st_uid != 0:
                raise ValueError('ACCOUNT_APPEND_PROFILE_REQUIRED')
            receipt = json.loads(private_bytes(pending, 4096))
            if (not isinstance(receipt, dict)
                    or set(receipt) != {'schema', 'table', 'beforeSize', 'beforeSha256', 'suffix'}
                    or type(receipt['schema']) is not int or receipt['schema'] != 1
                    or not isinstance(receipt['table'], str) or receipt['table'] not in TABLES
                    or type(receipt['beforeSize']) is not int or not 0 <= receipt['beforeSize'] <= LIMIT
                    or not isinstance(receipt['beforeSha256'], str)
                    or not re.fullmatch('[0-9a-f]{64}', receipt['beforeSha256'])
                    or not isinstance(receipt['suffix'], str)):
                raise ValueError('ACCOUNT_APPEND_RECEIPT')
            # Authenticate the recovery suffix against exact rows for the saved
            # service identities before using any bytes as account-file data.
            expected, _ = plan_accounts({key: '' for key in TABLES}, identities)
            key = receipt['table']
            rows = expected[key].splitlines(keepends=True)
            suffix = receipt['suffix']
            if suffix not in {rows[0], rows[1], ''.join(rows)}:
                raise ValueError('ACCOUNT_APPEND_SUFFIX')
            current = tables[key].encode('utf-8')
            size = receipt['beforeSize']
            prefix, tail = current[:size], current[size:]
            wanted = suffix.encode('utf-8')
            if (len(current) < size or size + len(wanted) > LIMIT
                    or hashlib.sha256(prefix).hexdigest() != receipt['beforeSha256']
                    or not wanted.startswith(tail)):
                raise ValueError('ACCOUNT_APPEND_CONFLICT')
            # Revalidate the whole recovered table. Never complete a receipt
            # that would introduce malformed rows or a conflicting identity.
            repaired = dict(tables)
            repaired[key] = (prefix + wanted).decode('utf-8')
            plan_accounts(repaired, identities)
            if tail != wanted:
                write_suffix(key, tables[key], wanted[len(tail):].decode('utf-8'))
            tables[key] = repaired[key]
            clear_pending()

        def append(key, old, suffix):
            before = old.encode('utf-8')
            if len(before) + len(suffix.encode('utf-8')) > LIMIT:
                raise ValueError('ACCOUNT_TABLE_TOO_LARGE')
            receipt = {'schema': 1, 'table': key, 'beforeSize': len(before),
                       'beforeSha256': hashlib.sha256(before).hexdigest(), 'suffix': suffix}
            atomic_replace(pending, json.dumps(receipt, sort_keys=True).encode())
            write_suffix(key, old, suffix)
            clear_pending()
        return provision_accounts(tables, identities, save, append)
