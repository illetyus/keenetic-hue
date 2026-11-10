"""Independent native owners for a selected release. No USB access in the manager."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from .lifecycle_events import emit,safe_reason,unavailable_reason

SYSTEM=Path('/opt/var/lib/keenetic-hue-system')
PYTHON='/opt/bin/python3'
SHELL='/opt/bin/sh' if Path('/opt/bin/sh').is_file() else '/bin/sh'
_spawned={}
_spawned_lock=threading.Lock()


def private_bytes(path,limit=8192):
    path=Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('OWNER_SYMLINK')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit or os.name=='posix' and info.st_mode&0o077:
            raise ValueError('OWNER_PRIVATE_FILE')
        raw=os.read(fd,limit+1)
        if len(raw)>limit:raise ValueError('OWNER_SIZE')
        return raw
    finally:os.close(fd)


def valid_id(value):
    return isinstance(value,str) and re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',value)


def selection(root):
    value=json.loads(private_bytes(root/'active.json',4096))
    if (not isinstance(value,dict) or type(value.get('schema')) is not int or value['schema']!=1
            or set(value)-{'schema','releaseId','previousReleaseId'} or not valid_id(value.get('releaseId'))
            or 'previousReleaseId' in value and not valid_id(value['previousReleaseId'])):
        raise ValueError('OWNER_SELECTION')
    return value['releaseId']


def verify_selected(root,release_id):
    from .releases import verify_signed_manifest
    from .platform import runtime_for
    from .activation import verify_slot
    raw=private_bytes(root/'releases'/release_id/'verified-manifest.json',128*1024)
    key=private_bytes(root/'config/release-trust.pub',32)
    untrusted=json.loads(raw)
    # Authenticate before using names to query installed package metadata.
    manifest=verify_signed_manifest(raw,key,'mips-3.4',f'cp{sys.version_info.major}{sys.version_info.minor}',untrusted.get('dependencies'))
    runtime=runtime_for(manifest['dependencies']);verify_slot(root,release_id,key,runtime)
    return release_id


def agent_paths(root):
    return {name:str(root/folder/file) for name,folder,file in (
        ('device_config','config','device.json'),('keystore','secrets','bond.json'),
        ('token_file','secrets','api.token'),('journal','state','commands.sqlite'),
        ('lock_file','run','adapter.lock'),('pid_file','run','agent.pid'),('log_file','logs','agent.log'))}


def validate_agent_config(root,config):
    expected=agent_paths(root)
    if (not isinstance(config,dict) or set(config)!=set(expected)|{'transport','address','controller_public_address','port','cloud','tunnel'}
            or any(config.get(k)!=v for k,v in expected.items())
            or type(config.get('port')) is not int or config['port']!=8787
            or not isinstance(config.get('cloud'),dict) or set(config['cloud'])!={'enabled'} or config['cloud']['enabled'] is not False
            or config.get('tunnel')!={'enabled':True,'port':8788,'token_file':str(root/'secrets/device.token')}):
        raise ValueError('OWNER_CONFIG_LAYOUT')
    for name in ('transport','address','controller_public_address'):
        if not isinstance(config[name],str) or not 1<=len(config[name])<=128 or not config[name].isascii():
            raise ValueError('OWNER_CONFIG_IDENTITY')
    if not config['transport'].startswith('usb:'):raise ValueError('OWNER_TRANSPORT')
    for name in expected:
        path=Path(config[name])
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('OWNER_CONFIG_SYMLINK')


def boot_id():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def owner_record(component,root=SYSTEM):
    path=root/'run'/f'{component}-supervisor.json'
    if not path.exists() and not path.is_symlink():return None
    value=json.loads(private_bytes(path,4096))
    if (not isinstance(value,dict) or set(value)!={'schema','pid','releaseId','bootId','startTicks','ownerFd'}
            or type(value['schema']) is not int or value['schema']!=1 or type(value['pid']) is not int
            or not 1<value['pid']<4194304 or not valid_id(value['releaseId'])
            or not isinstance(value['bootId'],str) or not re.fullmatch('[a-f0-9-]{36}',value['bootId'])
            or not isinstance(value['startTicks'],str) or not re.fullmatch('[0-9]{1,24}',value['startTicks'])
            or type(value['ownerFd']) is not int or not 3<=value['ownerFd']<=1048576):
        raise ValueError('OWNER_RECORD')
    return value


def process(pid):
    try:
        fields=Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()
        if fields[0]=='Z':return None
        return fields[19],Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    except (FileNotFoundError,ProcessLookupError):return None


def supervisor_owned(component,root,record):
    if not record or record['bootId']!=boot_id():return False
    observed=process(record['pid'])
    expected=[SHELL.encode(),str(root/'releases'/record['releaseId']/'service/hue-supervise.sh').encode(),component.encode(),str(root).encode(),record['releaseId'].encode(),str(record['ownerFd']).encode()]
    return bool(observed and observed==(record['startTicks'],expected))


def child_pid(component,root=SYSTEM):
    path=root/'run'/f'{component}-child.pid'
    try:
        value=private_bytes(path,32).decode().strip()
        if not value.isascii() or not value.isdigit() or not 1<int(value)<4194304:return None
        return int(value) if process(int(value)) else None
    except FileNotFoundError:return None


def child_owned(component,root,record,pid):
    observed=process(pid)
    if not observed or not record or record['bootId']!=boot_id():return False
    args=[value.decode() for value in observed[1]]
    release=root/'releases'/record['releaseId']
    try:
        from .runtime_policy import release_policy
        policy=release_policy(root,record['releaseId'])
        if policy.get('childIdentity'):
            from .child_identity import child_identity_matches
            if not child_identity_matches(root,component,record['releaseId'],pid,observed[0],record['bootId']):return False
    except (OSError,ValueError,KeyError,TypeError):return False
    wrapper=[PYTHON,'-I',str(release/'service/component-child.py'),component,str(root),record['releaseId'],str(record['ownerFd'])]
    if args==wrapper:return True
    if component=='agent':
        try:
            with Path(f'/proc/{pid}/environ').open('rb') as source:raw=source.read(65537)
            if len(raw)>65536:return False
            entries=[item.split(b'=',1) for item in raw.split(b'\0') if b'=' in item]
            env=dict(entries)
            if len(entries)!=len(env):return False
            status=Path(f'/proc/{pid}/status').read_text()
            if policy['agent']=='dedicated':
                from .agent_runtime import agent_process_matches
                from .privileges import load_service_identity
                identity=load_service_identity(root,'hue-agent')
                return agent_process_matches(root,record['releaseId'],args,status,env,
                    uid=identity['uid'],gid=identity['gid'],python=PYTHON)
            expected=[PYTHON,'-u','-m','hue_agent','--config',str(root/'config/agent.json')]
            rows=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
            if (not args or Path(args[0]).resolve()!=Path(PYTHON).resolve() or args[1:]!=expected[1:]
                    or list(map(int,rows['Uid'].split()))!=[0]*4
                    or list(map(int,rows['Gid'].split()))!=[0]*4):return False
            return env.get(b'HUE_RELEASE_ID')==record['releaseId'].encode() and env.get(b'PYTHONPATH')==str(release/'agent').encode()
        except (OSError,ValueError,KeyError,TypeError):return False
    from .privileges import load_tunnel_identity,runtime_paths,process_matches_identity,tunnel_arguments as restricted_arguments
    try:
        from .releases import verify_signed_manifest
        raw=private_bytes(release/'verified-manifest.json',128*1024)
        value=json.loads(raw)
        if not isinstance(value,dict):return False
        manifest=verify_signed_manifest(raw,private_bytes(root/'config/release-trust.pub',32),
            value.get('architecture'),value.get('pythonAbi'),value.get('dependencies'))
        if manifest['releaseId']!=record['releaseId']:return False
        # An explicitly selected signed legacy slot retains its old root-owned
        # contract during rollback. A restricted slot can never fall back to it:
        # contract selection is authenticated, not inferred from a failed drop.
        if 'manager/hue_admin/privileges.py' not in manifest['files']:
            expected=[str(release/'bin/cloudflared'),*tunnel_arguments(root)]
            status=Path(f'/proc/{pid}/status').read_text()
            rows=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
            if (args!=expected or list(map(int,rows['Uid'].split()))!=[0]*4
                    or list(map(int,rows['Gid'].split()))!=[0]*4):return False
            with (release/'bin/cloudflared').open('rb') as binary:return binary.read(4)==b'\x7fELF'
        identity=load_tunnel_identity(root);paths=runtime_paths(root,record['releaseId'])
        expected=[str(paths['binary']),*restricted_arguments(paths['token'],paths['log'])]
        status=Path(f'/proc/{pid}/status').read_text()
        # The signed RC5 Tunnel launcher predates bounding-set reduction.
        legacy_rc5=(manifest['releaseId']=='0.2.0-rc.5' and manifest.get('releaseSequence')==5
                    and 'metadata/runtime-policy.json' not in manifest['files'])
        if args!=expected or not process_matches_identity(
                status,identity['uid'],identity['gid'],require_empty_bounding=not legacy_rc5):return False
        with paths['binary'].open('rb') as binary:header=binary.read(4)
        return header==b'\x7fELF'
    except (OSError,ValueError,KeyError,TypeError):return False


def tunnel_arguments(root):
    return ['tunnel','--no-autoupdate','--protocol','quic','--edge-ip-version','4','--metrics','127.0.0.1:18790',
            '--loglevel','warn','--logfile',str(root/'logs/tunnel.log'),'run','--token-file',str(root/'secrets/tunnel.token')]


@contextmanager
def file_lock(path,wait=0):
    import fcntl
    fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0)|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:raise ValueError('OWNER_LOCK_FILE')
        deadline=time.monotonic()+wait
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise
                time.sleep(.05)
        yield fd
    finally:os.close(fd)


def stop_orphan(component,root,record):
    path=root/'run'/f'{component}-child.pid'
    if path.is_symlink():return False
    if record and record['bootId']!=boot_id():
        path.unlink(missing_ok=True);return True  # Previous boot IDs never authorize signalling current PIDs.
    pid=child_pid(component,root)
    if pid is None:path.unlink(missing_ok=True);return True
    if not child_owned(component,root,record,pid):return False
    before=process(pid)
    if not child_owned(component,root,record,pid) or process(pid)!=before:return False
    try:os.kill(pid,signal.SIGTERM)
    except ProcessLookupError:pass
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        current=process(pid)
        if current is None or current[0]!=before[0]:path.unlink(missing_ok=True);return True
        time.sleep(.1)
    return False


def environment(root):
    value=dict(os.environ)
    value.update(PATH='/opt/bin:/opt/sbin:/usr/bin:/bin:/usr/sbin:/sbin',HOME=str(root),
                 PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',GOMAXPROCS='1',HUE_PYTHON=PYTHON)
    for ca in ('/opt/etc/ssl/cert.pem','/opt/etc/ssl/certs/ca-certificates.crt','/etc/ssl/certs/ca-certificates.crt'):
        if Path(ca).is_file():value['SSL_CERT_FILE']=ca;break
    return value


def spawn_supervisor(component,root,release_id,fd):
    child=subprocess.Popen([SHELL,str(root/'releases'/release_id/'service/hue-supervise.sh'),component,str(root),release_id,str(fd)],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                           start_new_session=True,close_fds=True,pass_fds=(fd,),env=environment(root))
    if child is not None:
        with _spawned_lock:
            previous=_spawned.get((str(root),component))
            if previous is not None:previous.poll()
            _spawned[str(root),component]=child
    return child


def finalize_stopped_agent(component,root,record):
    if component!='agent' or not record:return
    from .runtime_policy import release_policy
    if release_policy(root,record['releaseId'])['agent']!='dedicated':return
    from .agent_runtime import checkpoint_agent_state
    from .usb_permissions import restore_usb_permissions
    checkpoint_agent_state(root,record['releaseId'])
    restore_usb_permissions(root,record['releaseId'])


def start_locked(component,root):
    stage='selection';release_id=None;attempt=None
    def failed(reason):
        emit(root,component,'operation_failed',stage,reason,release_id=release_id,attempt=attempt)
        return False
    try:
        release_id=selection(root);stage='owner_check';record=owner_record(component,root)
        if supervisor_owned(component,root,record):
            if record['releaseId']!=release_id:return False
            emit(root,component,'operation_succeeded','startup','OK',release_id=release_id)
            return True
        if record and unavailable_reason(root,component,record,release_id)=='SUPERVISOR_EXIT_UNKNOWN':
            emit(root,component,'prior_owner_unrecorded','supervisor','SUPERVISOR_EXIT_UNKNOWN',owner=record)
        stage='owner_lock'
        with file_lock(root/'run'/f'{component}-owner.lock') as fd:
            stage='orphan'
            if not stop_orphan(component,root,record):return failed('ORPHAN_CHECK_FAILED')
            stage='checkpoint'
            finalize_stopped_agent(component,root,record)
            stage='verify_release'
            if verify_selected(root,release_id)!=release_id or selection(root)!=release_id:return failed('RELEASE_CHANGED')
            stage='spawn'
            child=spawn_supervisor(component,root,release_id,fd)
            # Diagnostic identity lookup must not change the launch result.
            try:
                observed=process(child.pid) if child is not None else None
                if observed:attempt={'pid':child.pid,'startTicks':observed[0]}
            except OSError:pass
            stage='owner_wait'
            deadline=time.monotonic()+7
            while time.monotonic()<deadline:
                if supervisor_owned(component,root,owner_record(component,root)):
                    emit(root,component,'operation_succeeded','startup','OK',release_id=release_id)
                    return True
                time.sleep(.05)
            return failed('OWNER_WAIT_TIMEOUT')
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as error:
        return failed(safe_reason(error))


def stop_locked(component,root):
    record=owner_record(component,root)
    if supervisor_owned(component,root,record):
        if not supervisor_owned(component,root,record):return False
        try:os.kill(record['pid'],signal.SIGTERM)
        except ProcessLookupError:pass
        deadline=time.monotonic()+20
        while supervisor_owned(component,root,record):
            if time.monotonic()>=deadline:return False
            time.sleep(.1)
    with file_lock(root/'run'/f'{component}-owner.lock',wait=1):
        if not stop_orphan(component,root,record):return False
        finalize_stopped_agent(component,root,record)
        (root/'run'/f'{component}-child.json').unlink(missing_ok=True)
        (root/'run'/f'{component}-supervisor.json').unlink(missing_ok=True)
    with _spawned_lock:
        child=_spawned.pop((str(root),component),None)
        if child is not None:child.wait(timeout=2)
    return True


def action(component,verb,root=SYSTEM):
    if component not in ('agent','tunnel') or verb not in ('start','stop','restart','status'):return False
    root=Path(root).absolute()
    try:
        if root.is_symlink() or (root/'run').is_symlink() or any(p.is_symlink() for p in root.parents):return False
        with file_lock(root/'run'/f'{component}-operation.lock',wait=35):
            if verb=='status':return supervisor_owned(component,root,owner_record(component,root))
            if verb=='stop':
                result=stop_locked(component,root)
                emit(root,component,'operation_succeeded' if result else 'operation_failed','stop','OK' if result else 'ORPHAN_CHECK_FAILED')
                return result
            if verb=='restart' and not stop_locked(component,root):return False
            return start_locked(component,root)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as error:
        emit(root,component,'operation_failed','stop' if verb in ('stop','restart') else 'operation_lock',safe_reason(error))
        return False
