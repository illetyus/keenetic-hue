"""Start one detached ash supervisor, with a lifetime flock and no secret argv."""
import fcntl
import os
import subprocess
from pathlib import Path

BASE = Path('/opt/var/lib/hue-tunnel')
SERVICE = '/opt/etc/init.d/S98hue-tunnel'


def main():
    os.umask(0o077)
    BASE.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(BASE / 'supervisor.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if fd != 9:
            os.dup2(fd, 9)
            os.close(fd)
            fd = 9
        os.set_inheritable(fd, True)
        subprocess.Popen(
            ['/bin/sh', SERVICE, 'supervise'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True, pass_fds=(fd,),
        )
        return 0
    finally:
        os.close(fd)


if __name__ == '__main__':
    raise SystemExit(main())
