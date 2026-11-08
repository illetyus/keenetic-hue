"""Trusted RAM operation launch and read-only outcome monitoring.

No public CLI, PID signalling or recovery bypass. Launch only after reviewed
RAM preparation and operator recovery access; the live adapter owns mutations.
"""
import json
import os
from pathlib import Path
import secrets
import stat
import time
import uuid

from .acceptance_journal import boot_id, process_token, _valid_token
from .manifest import unique_object
from .storage_journal import RamJournal
from .storage_owner import launch_maintenance
from .storage_session import prepare


def summarize(state, code, alive):
    completed = (code == 0 and type(code) is int and state is not None
                 and state.get('phase') == 'closed' and state.get('clean') is True
                 and state.get('restoreVerified') is True)
    return {'terminal': code is not None or alive is False, 'completed': completed,
            'phase': ('completed' if completed else 'failed') if code is not None
                     else ('running' if alive is True else 'interrupted' if alive is False else 'unknown'),
            'maintenancePhase': state.get('phase') if state else None,
            'exitCode': code}


def wait_origins(tokens, timeout=30):
    deadline=time.monotonic()+timeout
    while any(process_token(token['pid'])==token for token in tokens):
        if time.monotonic()>=deadline:raise RuntimeError('STORAGE_ORIGIN_STILL_ACTIVE')
        time.sleep(.05)


def origins(io):
    """Observe launching process and USB-dependent ancestors, except listener.

    The original SSH session must exit normally; no process is terminated.
    Other user sessions remain holders and make LiveIO abort safely.
    """
    values=[];pid=os.getpid();seen=set()
    while pid>1 and pid not in seen:
        seen.add(pid);base=Path('/proc')/str(pid)
        token=process_token(pid)
        if token is None:break
        if pid==os.getpid() or (pid!=io.listener['pid'] and (base/'exe').stat().st_dev==io.device):
            values.append(token)
        pid=int((base/'stat').read_text().rsplit(') ',1)[1].split()[1])
    return tuple(values)


def _read(directory, name):
    journal=RamJournal(directory);root=journal._directory()
    try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
    finally:os.close(root)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>4096):
            raise ValueError('STORAGE_OPERATION_RECORD')
        data=os.read(fd,4097)
        if len(data)>4096:raise ValueError('STORAGE_OPERATION_RECORD')
        value=json.loads(data,object_pairs_hook=unique_object)
        if type(value) is not dict or value.get('schema')!=1 or value.get('bootId')!=boot_id():
            raise ValueError('STORAGE_OPERATION_BOOT')
        return value
    finally:os.close(fd)


def _write(directory, name, value):
    journal=RamJournal(directory);root=journal._directory()
    temporary='.operation-'+secrets.token_hex(16);created=False
    try:
        data=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        if len(data)>4096:raise ValueError('STORAGE_OPERATION_RECORD')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=root);created=True
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,name,src_dir_fd=root,dst_dir_fd=root);created=False;os.fsync(root)
        if _read(directory,name)!=value:raise ValueError('STORAGE_OPERATION_READBACK')
    finally:
        if created:os.unlink(temporary,dir_fd=root)
        os.close(root)


def start(io, root=Path('/tmp/hue-storage-operations'), *, origin_tokens=None):
    """Internal trusted call. Started PID is not completion proof."""
    import fcntl
    root=Path(root).absolute()
    root.mkdir(mode=0o700,exist_ok=True)
    directory_fd=RamJournal(root)._directory()
    try:lock=os.open('launch.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=directory_fd)
    finally:os.close(directory_fd)
    try:
        info=os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o600):raise ValueError('STORAGE_OPERATION_LOCK')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('STORAGE_OPERATION_BUSY') from None
        tokens=origins(io) if origin_tokens is None else origin_tokens
        identifier=str(uuid.uuid4());directory=root/identifier;directory.mkdir(mode=0o700)
        journal=RamJournal(directory)
        meta={'schema':1,'bootId':boot_id(),'operationId':identifier,'guardian':None}
        _write(directory,'owner.json',meta)
        def finished(code):
            _write(directory,'result.json',{'schema':1,'bootId':boot_id(),'exitCode':code})
        guardian=launch_maintenance(io,journal,before_run=lambda:wait_origins(tokens),
                                    on_exit=finished,preserve_fds=(lock,))
        meta['guardian']=process_token(guardian)
        _write(directory,'owner.json',meta)
        return {'started':True,'operationId':identifier,'directory':str(directory),'guardianPid':guardian}
    finally:os.close(lock)


def status(directory):
    directory=Path(directory);meta=_read(directory,'owner.json')
    if (set(meta)!={'schema','bootId','operationId','guardian'} or type(meta['schema']) is not int
            or type(meta['operationId']) is not str or str(uuid.UUID(meta['operationId']))!=meta['operationId']):
        raise ValueError('STORAGE_OPERATION_OWNER')
    token=meta['guardian']
    if token is not None and (not isinstance(token,dict) or not _valid_token(token,token.get('pid'))):
        raise ValueError('STORAGE_OPERATION_OWNER')
    alive=None if token is None else process_token(token['pid'])==token
    try:state=RamJournal(directory).load()
    except FileNotFoundError:state=None
    try:result=_read(directory,'result.json')
    except FileNotFoundError:code=None
    else:
        if (set(result)!={'schema','bootId','exitCode'} or type(result['schema']) is not int
                or type(result['exitCode']) is not int or result['exitCode'] not in (0,1,124)):
            raise ValueError('STORAGE_OPERATION_RESULT')
        code=result['exitCode']
    return {'schema':1,'operationId':meta['operationId'],**summarize(state,code,alive)}


def dispatch(arguments, runtime, factory):
    """Fixed root-private RAM entry; no paths, commands or force flags from CLI."""
    if type(arguments) is not list or any(type(v) is not str for v in arguments):
        raise ValueError('STORAGE_OPERATION_ARGUMENTS')
    if len(arguments)==2 and arguments[0]=='status':
        identifier=arguments[1]
        try:valid=str(uuid.UUID(identifier))==identifier
        except ValueError:valid=False
        if not valid:raise ValueError('STORAGE_OPERATION_ARGUMENTS')
        return status(Path('/tmp/hue-storage-operations')/identifier)
    if arguments not in ([],['prepare'],['start']):raise ValueError('STORAGE_OPERATION_ARGUMENTS')
    with prepare(runtime,factory) as io:
        if arguments==['start']:return start(io)
        return {'schema':1,'prepared':True,'servicesStopped':False,
                'deviceUnmounted':False,'realUsbFsckPerformed':False}
