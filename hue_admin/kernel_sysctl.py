"""Bounded sysctl file IO. No initialization or boot activation occurs here.

The caller supplies a trusted root directory, including its ancestors and
contents, and must serialize writers. Successful reads still require policy
validation. This module does not authorize changes, persist receipts, or prove
device acceptance.
"""
import os
import stat

from .kernel_policy import ASLR, CONTROLS

_FILES = {name: tuple(name.split('.')) for name in (*CONTROLS, ASLR)}


class SysctlFiles:
    def __init__(self, root):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        self.fds = {}
        parent = os.open(root, flags)
        try:
            for name in ('fs', 'kernel'):
                self.fds[name] = os.open(name, flags, dir_fd=parent)
        except BaseException:
            self.close()
            raise
        finally:
            os.close(parent)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()

    def _open(self, name, flags):
        if name not in _FILES:
            raise ValueError('KERNEL_CONTROL_NAME')
        directory, leaf = _FILES[name]
        fd = os.open(leaf, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fds[directory])
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError('KERNEL_CONTROL_TYPE')
        return fd

    def read(self, name):
        try:
            fd = self._open(name, os.O_RDONLY)
        except FileNotFoundError:
            if name in CONTROLS:
                return None
            raise
        try:
            data = os.read(fd, 16)
        finally:
            os.close(fd)
        if data not in (b'0\n', b'1\n', b'2\n', b'0', b'1', b'2'):
            raise ValueError('KERNEL_CONTROL_VALUE')
        return int(data)

    def write(self, name, value):
        if name not in CONTROLS or type(value) is not int or value not in (0, 1):
            raise ValueError('KERNEL_CONTROL_WRITE')
        fd = self._open(name, os.O_WRONLY)
        try:
            data = (str(value) + '\n').encode('ascii')
            if os.write(fd, data) != len(data):
                raise OSError('KERNEL_SHORT_WRITE')
        finally:
            os.close(fd)
