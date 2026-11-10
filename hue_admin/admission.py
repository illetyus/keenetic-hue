"""Admit one maintenance CLI before expensive imports; read-only checks remain available."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import time
from . import service_manager as manager


def already_managed(root,argv):
    if argv and argv[0]=='start':args=argv[1:]
    elif argv[:2]==['internal-service','start']:args=argv[2:]
    else:return False
    if len(args)>1 or args and args[0] not in ('agent','tunnel'):return False
    try:
        acceptance=root/'state/acceptance-current.json'
        if acceptance.exists() or acceptance.is_symlink():
            value=json.loads(manager.private_bytes(acceptance,16384))
            snapshot=value.get('snapshot') if isinstance(value,dict) else None
            if (not isinstance(snapshot,dict) or snapshot.get('phase')!='closed'
                    or snapshot.get('restoreVerified') is not True):return False
        # A published owner is not permission to skip an unfinished transaction.
        for name,terminal in (('migration.json',('committed','rolled_back')),('bootstrap.json',('committed',)),
                              ('initialize.json',('committed','rolled_back')),
                              ('activation.json',('committed','rolled_back')),('installation.json',('committed',)),
                              ('restore-pending/receipt.json',('done','rolled_back'))):
            path=root/'state'/name
            if path.exists() or path.is_symlink():
                if json.loads(manager.private_bytes(path,32768)).get('phase') not in terminal:return False
        selected=manager.selection(root)
        for component in args or ('agent','tunnel'):
            record=manager.owner_record(component,root)
            if not record or record['releaseId']!=selected or not manager.supervisor_owned(component,root,record):return False
        return True
    except (OSError,ValueError,KeyError,TypeError):return False


@contextmanager
def admit(root,argv,timeout=45):
    root=Path(root).absolute()
    if argv and (argv[0] in ('status','health','diagnose') or '--help' in argv or '-h' in argv):
        yield True;return
    if already_managed(root,argv):yield False;return
    if root.is_symlink() or any(p.is_symlink() for p in root.parents):raise ValueError('MANAGER_PATH')
    import fcntl
    fd=os.open(root/'cli-admission.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:raise ValueError('MANAGER_LOCK_FILE')
        deadline=time.monotonic()+timeout
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if already_managed(root,argv):yield False;return
                if time.monotonic()>=deadline:raise RuntimeError('MANAGER_BUSY')
                time.sleep(.05)
        if already_managed(root,argv):yield False;return
        yield True
    finally:os.close(fd)
