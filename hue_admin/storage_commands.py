"""Fixed, bounded RAM BusyBox commands for the trusted storage transaction.

The root-owned provisioner supplies hashes; no network request may supply this
lock or select a command. Verification does not perform a mount operation.
"""
import copy
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

from .owned_command import run_owned


class RamCommands:
    def __init__(self, busybox, libraries, lock, node, storage):
        self.busybox = Path(busybox)
        self.libraries = Path(libraries)
        if (not PurePosixPath(busybox).is_absolute() or self.busybox.name != 'busybox'
                or self.libraries != self.busybox.parent / 'lib'
                or not re.fullmatch(r'/dev/sd[a-z][1-9][0-9]?', node)
                or not re.fullmatch(r'/tmp/mnt/[A-Za-z0-9_-]{1,128}', storage)
                or not isinstance(lock, dict) or set(lock) != {'files', 'links'}):
            raise ValueError('STORAGE_COMMAND_CONFIGURATION')
        self.lock = copy.deepcopy(lock)
        self.allowed = {
            ('umount', '/opt'), ('umount', storage),
            ('mount', '-t', 'ext4', '-o', 'rw,noatime,data=ordered', node, storage),
            ('mount', '-t', 'ext4', '-o', 'ro,noload,noatime', node, storage),
            ('mount', '-o', 'bind', storage, '/opt'),
            ('mount', '-o', 'remount,bind,ro', '/opt'),
        }

    def prefix(self):
        return [str(self.libraries / 'ld.so.1'), '--library-path',
                str(self.libraries), str(self.busybox)]

    def verify(self):
        root = self.busybox.parent
        if (os.geteuid() != 0 or not root.is_relative_to(Path('/tmp'))
                or '..' in root.parts or any(p.is_symlink() for p in (root, *root.parents))):
            raise ValueError('STORAGE_COMMAND_LOCATION')
        for directory in (root, self.libraries):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('STORAGE_COMMAND_OWNER')
        files, links = self.lock['files'], self.lock['links']
        valid = lambda name: type(name) is str and (name == 'busybox' or re.fullmatch(r'lib/(?:ld\.so\.1|lib[A-Za-z0-9_+.-]+\.so(?:\.[0-9]+)*)', name))
        if (not isinstance(files, dict) or not isinstance(links, dict)
                or not 2 <= len(files) <= 48 or len(links) > 48
                or not {'busybox', 'lib/ld.so.1'} <= files.keys()
                or any(not valid(k) or type(v) is not str or not re.fullmatch('[a-f0-9]{64}', v) for k, v in files.items())
                or any(not valid(k) or k in files or k in ('busybox', 'lib/ld.so.1')
                       or type(v) is not str or 'lib/' + v not in files for k, v in links.items())):
            raise ValueError('STORAGE_COMMAND_LOCK')
        expected = {name[4:] for name in files.keys() | links.keys() if name.startswith('lib/')}
        if {p.name for p in self.libraries.iterdir()} != expected:
            raise ValueError('STORAGE_COMMAND_CONTENTS')
        for name, digest in files.items():
            path = root / name
            info = path.lstat()
            mode = 0o700 if name in ('busybox', 'lib/ld.so.1') else 0o600
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != mode or info.st_size > 32 * 1024 * 1024
                    or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                raise ValueError('STORAGE_COMMAND_FILE')
        for name, target in links.items():
            path = root / name
            if not path.is_symlink() or path.lstat().st_uid != 0 or os.readlink(path) != target:
                raise ValueError('STORAGE_COMMAND_LINK')
        listing = run_owned(self.prefix()[:3] + ['--list', str(self.busybox)],
                            timeout=15, cwd=str(root))
        if listing['exitCode'] or listing['outputBytes'] > 8192:
            raise ValueError('STORAGE_COMMAND_DEPENDENCIES')
        for line in listing['outputTail'].splitlines():
            if not line.strip(): continue
            candidate = (line.split('=>', 1)[1] if '=>' in line else line).strip().split()[0]
            if candidate.startswith(('linux-vdso', 'linux-gate')): continue
            path = Path(candidate)
            if path.parent != self.libraries or 'lib/' + path.name not in files.keys() | links.keys():
                raise ValueError('STORAGE_COMMAND_EXTERNAL_DEPENDENCY')
        return True

    def __call__(self, arguments):
        if type(arguments) is not tuple or any(type(v) is not str for v in arguments) or arguments not in self.allowed:
            raise ValueError('STORAGE_COMMAND_ARGUMENTS')
        self.verify()
        return run_owned(self.prefix() + list(arguments), timeout=30,
                         cwd=str(self.busybox.parent))['exitCode'] == 0
