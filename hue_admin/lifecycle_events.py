"""Bounded root lifecycle diagnostics. Never serialize exceptions or payloads."""
import datetime
import json
import math
import os
from pathlib import Path
import re
import stat
import time

LOG_LIMIT=65536
EVENTS={'operation_failed','operation_succeeded','dependency_blocked','prior_owner_unrecorded',
        'supervisor_start','supervisor_signal','supervisor_exit','child_exit'}
STAGES={'selection','owner_check','owner_lock','orphan','checkpoint','verify_release','spawn',
        'owner_wait','startup','operation_lock','stop','dependency','supervisor'}
REASONS={'OK','OS_ERROR','VALUE_ERROR','RUNTIME_ERROR','INTERNAL_ERROR','LOCK_BUSY','OWNER_WAIT_TIMEOUT',
         'ORPHAN_CHECK_FAILED','RELEASE_CHANGED','AGENT_START_FAILED','SUPERVISOR_EXIT_UNKNOWN',
         'OBSERVED_TERM','OBSERVED_INT','OBSERVED_EXIT','CHILD_EXIT',
         'AGENT_CHECKPOINT_REQUIRES_STOP','AGENT_RESTRICTED_POLICY_REQUIRED','AGENT_CHECKPOINT_BOND',
         'AGENT_RECOVERY_ROOT','AGENT_RECOVERY_PATH','AGENT_RECOVERY_PARENT','AGENT_RECOVERY_FILE',
         'AGENT_RECOVERY_SIZE','AGENT_RECOVERY_SPACE','AGENT_RECOVERY_CHANGED',
         'AGENT_RECOVERY_UNSUPPORTED_SIDECAR','AGENT_RECOVERY_STAGE_PARENT','AGENT_RECOVERY_AUTHORIZATION',
         'OWNER_SELECTION','OWNER_RECORD','OWNER_LOCK_FILE','OWNER_SYMLINK','OWNER_PRIVATE_FILE'}
SNAPSHOT=re.compile(r'AGENT_DATABASE_SNAPSHOT_FAILED:(?:IDENTITY|REFERENCE|OPEN|SCHEMA|BACKUP|OUTPUT|RECOVERY_INPUT|RECOVERY|TIMEOUT|START|WORKER_EXIT_-?\d{1,3})\Z')


def safe_reason(error):
    value=str(error)
    if value in REASONS or SNAPSHOT.fullmatch(value):return value
    if isinstance(error,BlockingIOError):return 'LOCK_BUSY'
    if isinstance(error,OSError):return 'OS_ERROR'
    if isinstance(error,ValueError):return 'VALUE_ERROR'
    if isinstance(error,RuntimeError):return 'RUNTIME_ERROR'
    return 'INTERNAL_ERROR'


def _boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def _validate(value):
    required={'schema','component','event','stage','reason','bootId','pid','startTicks','utc','monotonic'}
    if not isinstance(value,dict) or not required<=set(value) or set(value)-required-{'releaseId','exitCode','signal','retrySeconds','observedBootId','attemptPid','attemptStartTicks'}:raise ValueError()
    if type(value['schema']) is not int or value['schema']!=1 or value['component'] not in ('agent','tunnel') or value['event'] not in EVENTS or value['stage'] not in STAGES:raise ValueError()
    if value['reason'] not in REASONS and not SNAPSHOT.fullmatch(value['reason']):raise ValueError()
    if not re.fullmatch('[a-f0-9-]{36}',value['bootId']) or type(value['pid']) is not int or not 1<=value['pid']<4194304:raise ValueError()
    if not re.fullmatch('[0-9]{1,24}',value['startTicks']) or not re.fullmatch(r'[0-9TZ:.+\-]{20,40}',value['utc']):raise ValueError()
    if type(value['monotonic']) not in (int,float) or not math.isfinite(value['monotonic']) or value['monotonic']<0:raise ValueError()
    if 'releaseId' in value and not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',value['releaseId']):raise ValueError()
    if 'signal' in value and value['signal'] not in ('','TERM','INT'):raise ValueError()
    if 'exitCode' in value and (type(value['exitCode']) is not int or not 0<=value['exitCode']<=255):raise ValueError()
    if 'retrySeconds' in value and (type(value['retrySeconds']) is not int or not 0<=value['retrySeconds']<=30):raise ValueError()
    if 'observedBootId' in value and not re.fullmatch('[a-f0-9-]{36}',value['observedBootId']):raise ValueError()
    if ('attemptPid' in value)!=('attemptStartTicks' in value):raise ValueError()
    if 'attemptPid' in value and (type(value['attemptPid']) is not int or not 1<=value['attemptPid']<4194304 or not re.fullmatch('[0-9]{1,24}',value['attemptStartTicks'])):raise ValueError()
    return value


def _private_directory(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError()
    info=path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError()


def _read(path):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError()
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>4096:raise ValueError()
        return _validate(json.loads(os.read(fd,4097)))
    finally:os.close(fd)


def emit(root,component,event,stage,reason,*,owner=None,release_id=None,attempt=None):
    """Best effort: logging failure must never change a lifecycle decision."""
    try:
        import fcntl
        from .restore_transaction import atomic_replace
        root=Path(root)
        _private_directory(root/'run');_private_directory(root/'logs')
        current_boot=_boot();pid=os.getpid()
        ticks=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
        value={'schema':1,'component':component,'event':event,'stage':stage,'reason':reason,
               'bootId':current_boot,'pid':pid,'startTicks':ticks,
               'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'monotonic':time.monotonic()}
        if owner:
            value.update({k:owner[k] for k in ('bootId','pid','startTicks','releaseId')})
            value['observedBootId']=current_boot
        if release_id is not None:value['releaseId']=release_id
        if attempt is not None:value.update(attemptPid=attempt['pid'],attemptStartTicks=attempt['startTicks'])
        _validate(value);line=(json.dumps(value,separators=(',',':'))+'\n').encode()
        lock=os.open(root/'run/lifecycle-log.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try:
            info=os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:return False
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            log=root/'logs/lifecycle.log'
            fd=os.open(log,os.O_APPEND|os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>LOG_LIMIT:return False
                if info.st_size+len(line)>LOG_LIMIT:
                    os.close(fd);fd=None
                    os.replace(log,log.with_suffix('.log.previous'))
                    fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                view=memoryview(line)
                while view:
                    count=os.write(fd,view)
                    if count<=0:raise OSError()
                    view=view[count:]
                os.fsync(fd)
            finally:
                if fd is not None:os.close(fd)
            if event in ('operation_failed','operation_succeeded','dependency_blocked'):
                atomic_replace(root/'run'/f'{component}-lifecycle.json',json.dumps(value).encode())
        finally:os.close(lock)
        return True
    except (OSError,ValueError,TypeError,KeyError,ImportError):return False


def last_failure(root,component):
    try:
        if component not in ('agent','tunnel'):return None
        value=_read(Path(root)/'run'/f'{component}-lifecycle.json')
        if value['component']==component and value['bootId']==_boot() and value['event'] in ('operation_failed','dependency_blocked'):return value
    except (OSError,ValueError,TypeError,KeyError):pass
    return None


def unavailable_reason(root,component,owner,selected):
    failure=last_failure(root,component)
    # A published owner's later terminal event supersedes an earlier launch
    # timeout, even when nobody repeated the successful start operation.
    if owner and owner.get('releaseId')==selected:
        try:
            terminal=_read(Path(root)/'run'/f'{component}-terminal.json')
            if (terminal['component']==component and terminal['event']=='supervisor_exit'
                    and all(terminal[k]==owner[k] for k in ('bootId','pid','startTicks','releaseId'))
                    and (not failure or terminal['bootId']==failure['bootId'] and terminal['monotonic']>=failure['monotonic'])):
                return 'SUPERVISOR_'+(terminal['signal'] if terminal.get('signal') else 'EXIT_RECORDED')
        except (OSError,ValueError,TypeError,KeyError):pass
        if (failure and failure['stage']=='owner_wait' and failure['bootId']==owner['bootId']
                and failure.get('attemptPid')==owner['pid'] and failure.get('attemptStartTicks')==owner['startTicks']):failure=None
    if failure and (failure.get('releaseId') in (None,selected)):
        return failure['stage'].upper()+':'+failure['reason']
    if not owner:return 'OWNER_OR_VERSION_UNAVAILABLE'
    if owner.get('releaseId')!=selected:return 'OWNER_VERSION_MISMATCH'
    try:
        from .service_manager import process
        if owner['bootId']==_boot() and process(owner['pid']) is not None:return 'OWNER_IDENTITY_MISMATCH'
        terminal=_read(Path(root)/'run'/f'{component}-terminal.json')
        if terminal['component']==component and terminal['event']=='supervisor_exit' and all(terminal[k]==owner[k] for k in ('bootId','pid','startTicks','releaseId')):
            return 'SUPERVISOR_'+(terminal['signal'] if terminal.get('signal') else 'EXIT_RECORDED')
    except (OSError,ValueError,TypeError,KeyError):pass
    return 'SUPERVISOR_EXIT_UNKNOWN'
