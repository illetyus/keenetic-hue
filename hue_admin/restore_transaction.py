"""Recoverable multi-file replacement. A pointer alone cannot make this atomic."""
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .manifest import validate_name, unique_object
from .restore import write_private
from .snapshot import read_private_bytes


def sync_directory(path):
    if os.name!='posix':return
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def atomic_replace(path, data):
    path=Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('RESTORE_SYMLINK')
    fd,name=tempfile.mkstemp(prefix='.hue-replace-',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        temporary.replace(path);sync_directory(path.parent)
    finally:
        if temporary.exists():temporary.unlink()


def _unlink_sqlite_sidecars(path):
    path=Path(path).absolute()
    for suffix in ('-wal','-shm','-journal'):
        side=Path(str(path)+suffix)
        if side.is_symlink() or any(p.is_symlink() for p in side.parents):
            raise ValueError('RESTORE_SYMLINK')
        if side.exists():
            if not side.is_file():raise ValueError('RESTORE_SIDECAR_TYPE')
            side.unlink();sync_directory(side.parent)


def record(directory, receipt):
    atomic_replace(directory/'receipt.json',json.dumps(receipt,sort_keys=True).encode('utf-8'))


def recover(targets, directory, stop, start):
    directory=Path(directory)
    receipt=json.loads(read_private_bytes(directory/'receipt.json',32768),object_pairs_hook=unique_object)
    if (not isinstance(receipt,dict) or receipt.get('schema')!=1
            or receipt.get('files')!=sorted(targets)
            or receipt.get('phase') not in ('prepared','ready','written','started','done','rolled_back')):
        raise ValueError('RESTORE_RECEIPT')
    if receipt['phase'] in ('done','rolled_back'):return
    if receipt['phase']!='prepared':
        hashes=receipt.get('before')
        if not isinstance(hashes,dict) or set(hashes)!=set(targets):raise ValueError('RESTORE_RECEIPT')
        before={name:read_private_bytes(directory/'before'/validate_name(name)) for name in targets}
        if any(hashlib.sha256(data).hexdigest()!=hashes[name] for name,data in before.items()):
            raise ValueError('RESTORE_RECOVERY_HASH')
        if not stop():raise RuntimeError('RESTORE_RECOVERY_STOP_FAILED')
        for name,target in targets.items():
            if name.endswith('.sqlite'):_unlink_sqlite_sidecars(target)
        for name,data in before.items():atomic_replace(targets[name],data)
    if not start():raise RuntimeError('RESTORE_RECOVERY_START_FAILED')
    receipt['phase']='rolled_back';record(directory,receipt)


def apply_files(files, targets, directory, stop, start, healthy, checkpoint=lambda:None, failpoint=lambda phase:None):
    if set(files)!=set(targets) or not files:
        raise ValueError('RESTORE_FILE_SET')
    for name in files:
        validate_name(name)
        if not isinstance(files[name],bytes):raise ValueError('RESTORE_CONTENT')
        read_private_bytes(targets[name])
    directory=Path(directory).absolute()
    if directory.is_symlink() or any(p.is_symlink() for p in directory.parents):
        raise ValueError('RESTORE_SYMLINK')
    directory.mkdir(mode=0o700)
    sync_directory(directory.parent)
    receipt={'schema':1,'phase':'prepared','files':sorted(targets)}
    record(directory,receipt);failpoint('prepared')
    try:
        if not stop():raise RuntimeError('RESTORE_STOP_FAILED')
        checkpoint()
        before={name:read_private_bytes(path) for name,path in targets.items()}
        (directory/'before').mkdir(mode=0o700)
        for category in {name.split('/')[0] for name in files}:
            (directory/'before'/category).mkdir(mode=0o700)
        for name,data in before.items():
            path=directory/'before'/name
            write_private(path,data);sync_directory(path.parent)
        sync_directory(directory/'before')
        receipt.update(phase='ready',before={n:hashlib.sha256(d).hexdigest() for n,d in before.items()})
        record(directory,receipt);failpoint('ready')
        for name,data in files.items():
            atomic_replace(targets[name],data);failpoint('write:'+name)
        receipt['phase']='written';record(directory,receipt)
        if not start():raise RuntimeError('RESTORE_START_FAILED')
        receipt['phase']='started';record(directory,receipt);failpoint('started')
        if not healthy():raise RuntimeError('RESTORE_HEALTH_FAILED')
        receipt['phase']='done';record(directory,receipt)
    except Exception:
        recover(targets,directory,stop,start)
        raise
    return {'restored':True,'files':len(files),'healthVerified':True}
