"""Private, durable receipt for the unconnected kernel transaction engine.

The caller must hold the management lock and supply a root whose ancestors are
trusted. This store does not authorize a kernel change or initiate recovery.
"""
import json
import os
import re
import secrets
import stat
from pathlib import Path

from .kernel_transaction import _validated

_NAME = 'kernel-transaction.json'


def _boot_id():
    value = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}', value):
        raise ValueError('KERNEL_BOOT_ID')
    return value


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('KERNEL_RECEIPT_DUPLICATE')
        value[key] = item
    return value


class KernelReceipt:
    def __init__(self, root):
        self.root = os.fspath(root)

    def _directory(self):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root = os.open(self.root, flags)
        try:
            info = os.fstat(root)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('KERNEL_RECEIPT_ROOT')
            state = os.open('state', flags, dir_fd=root)
        finally:
            os.close(root)
        info = os.fstat(state)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(state)
            raise ValueError('KERNEL_RECEIPT_DIRECTORY')
        return state

    def _read(self, directory, *, include_prior_boot=False):
        fd = os.open(_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 4096):
                raise ValueError('KERNEL_RECEIPT_FILE')
            data = b''
            while len(data) <= 4096:
                part = os.read(fd, 4097 - len(data))
                if not part:
                    break
                data += part
            if len(data) > 4096:
                raise ValueError('KERNEL_RECEIPT_FILE')
            value = json.loads(data, object_pairs_hook=_unique)
            if (not isinstance(value, dict) or set(value) != {'schema', 'bootId', 'transaction'}
                    or type(value['schema']) is not int or value['schema'] != 1
                    or not isinstance(value['bootId'], str)
                    or not re.fullmatch(r'[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}', value['bootId'])):
                raise ValueError('KERNEL_RECEIPT_ENVELOPE')
            if value['bootId'] != _boot_id() and not include_prior_boot:
                raise ValueError('KERNEL_RECEIPT_BOOT_CHANGED')
            transaction = _validated(value['transaction'])[0]
            return (transaction, value['bootId']) if include_prior_boot else transaction
        finally:
            os.close(fd)

    def load(self):
        directory = self._directory()
        try:
            return self._read(directory)
        finally:
            os.close(directory)

    def save(self, value):
        return self._save(value)

    def _save(self, value, *, previous_boot=None):
        value = _validated(value)[0]
        data = json.dumps({'schema': 1, 'bootId': _boot_id(), 'transaction': value},
                          sort_keys=True, separators=(',', ':')).encode('utf-8')
        directory = self._directory()
        temporary = '.kernel-receipt-' + secrets.token_hex(16)
        created = False
        try:
            try:
                if previous_boot is None:
                    self._read(directory)
                else:
                    _, observed_boot = self._read(directory, include_prior_boot=True)
                    if observed_boot != previous_boot:
                        raise ValueError('KERNEL_RECEIPT_CHANGED')
            except FileNotFoundError:
                if previous_boot is not None:
                    raise ValueError('KERNEL_RECEIPT_CHANGED') from None
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            created = True
            try:
                os.fchmod(fd, 0o600)
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError('KERNEL_RECEIPT_FILE')
                remaining = memoryview(data)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError('KERNEL_RECEIPT_WRITE')
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temporary, _NAME, src_dir_fd=directory, dst_dir_fd=directory)
            created = False
            os.fsync(directory)
        finally:
            if created:
                os.unlink(temporary, dir_fd=directory)
            os.close(directory)

    def reconcile_boot(self, read):
        """Retire an old receipt only if the entire original baseline returned.

        Requires the same trusted target, exclusive management lock and a
        verified selected release. Does not require kernel-apply permission
        and does not itself write kernel settings.
        """
        directory = self._directory()
        try:
            value, origin = self._read(directory, include_prior_boot=True)
        finally:
            os.close(directory)
        if origin == _boot_id():
            raise ValueError('KERNEL_RECEIPT_SAME_BOOT')
        for name, expected in value['observed'].items():
            actual = read(name)
            if type(actual) is not type(expected) or actual != expected:
                raise ValueError('KERNEL_BOOT_BASELINE_CHANGED')
        retired = {**value, 'phase': 'restored'}
        self._save(retired, previous_boot=origin)
        return retired
