"""Start one detached ash supervisor, with a lifetime flock and no secret argv."""
import fcntl
import os
import subprocess
import signal
import sys
import time
from pathlib import Path

BASE = Path('/opt/var/lib/hue-agent')
SERVICE = '/opt/etc/init.d/S99hue-agent-supervised'


def stop_orphan():
    path=BASE/'agent-child.pid'
    if not path.exists():return True
    if path.is_symlink():return False
    value=path.read_text().strip()
    if not value.isascii() or not value.isdigit() or not 1<int(value)<4194304:return False
    pid=int(value)
    def identity():
        try:
            fields=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()
            if fields[0]=='Z':return None
            return fields[19],Path(f'/proc/{pid}/cmdline').read_bytes()
        except FileNotFoundError:return None
    before=identity()
    if before is not None:
        args=before[1].rstrip(b'\0').decode().split('\0')
        wrapper=['-I',str(BASE / 'app' / 'agent-foreground.py')]
        agent=['-u','-m','hue_agent','--config',str(BASE/'config.json')]
        if (not args or Path(args[0]).resolve()!=Path(sys.executable).resolve()
                or args[1:] not in (wrapper,agent)):
            return False
        if identity()!=before:return False
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            current=identity()
            if current is None or current[0]!=before[0]:break
            time.sleep(0.1)
        else:return False
    if path.exists() and path.read_text().strip()==value:path.unlink()
    return True


def main():
    os.umask(0o077)
    fd = os.open(BASE / 'supervisor.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 1 if '--stop-orphan' in sys.argv else 0
        if not stop_orphan():return 1
        if '--stop-orphan' in sys.argv:return 0
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
