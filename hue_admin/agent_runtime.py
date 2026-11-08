"""Root prepares verified code; mutable agent state is never silently reseeded."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .agent_storage import agent_runtime_paths,read_agent_private,MAX_AGENT_FILE
from .manifest import unique_object
from .privileges import load_service_identity,projected_file
from .restore_transaction import atomic_replace,sync_directory
from .runtime_policy import authenticated_manifest,release_policy
from .service_manager import private_bytes,validate_agent_config,child_pid
from .usb_policy import pinned_transport


def _no_links(path):
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('AGENT_RUNTIME_SYMLINK')


def _root_directory(path,mode,gid=0):
    _no_links(path)
    try:path.mkdir(mode=0o700)
    except FileExistsError:pass
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        # Recover only root-owned, non-writable-to-service partial creation.
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_gid not in (0,gid)
                or stat.S_IMODE(info.st_mode) not in (0o700,mode)):
            raise ValueError('AGENT_RUNTIME_ROOT_DIRECTORY')
        os.fchown(fd,0,gid);os.fchmod(fd,mode)
    finally:os.close(fd)


def _owned_directory(path,uid,gid,*,required=False):
    _no_links(path)
    try:path.mkdir(mode=0o700)
    except FileExistsError:pass
    else:
        if required:
            path.rmdir()
            raise ValueError('AGENT_STATE_MISSING')
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o700:
            raise ValueError('AGENT_RUNTIME_OWN_DIRECTORY')
        if (info.st_uid,info.st_gid)==(0,0) and not any(path.iterdir()):
            os.fchown(fd,uid,gid)
        elif (info.st_uid,info.st_gid)!=(uid,gid):
            raise ValueError('AGENT_RUNTIME_OWN_DIRECTORY')
    finally:os.close(fd)


def _master(path,limit=65536):
    data=private_bytes(path,limit);info=path.lstat()
    if info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600:
        raise ValueError('AGENT_MASTER_OWNER')
    return data


def _marker(root,identity):
    path=root/'state/agent-runtime.json'
    if not path.exists() and not path.is_symlink():return None
    value=json.loads(_master(path,4096),object_pairs_hook=unique_object)
    if (not isinstance(value,dict) or set(value)!={'schema','phase','uid','gid','bondHash','journalHash'}
            or type(value['schema']) is not int or value['schema']!=1
            or value['phase'] not in ('prepared','ready','exported')
            or any(type(value[k]) is not int or value[k]!=identity[k] for k in ('uid','gid'))
            or any(not isinstance(value[k],str) or not re.fullmatch('[0-9a-f]{64}',value[k]) for k in ('bondHash','journalHash'))):
        raise ValueError('AGENT_RUNTIME_MARKER')
    return value


def _seed_file(path,data,uid,gid):
    _no_links(path)
    if path.exists():
        info=path.lstat()
        if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1
                or (info.st_uid,info.st_gid) not in ((0,0),(uid,gid))):
            raise ValueError('AGENT_SEED_TARGET')
        path.unlink()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as output:
        inode=os.fstat(output.fileno())
        output.write(data);output.flush();os.fchown(output.fileno(),uid,gid);os.fsync(output.fileno())
    current=path.lstat()
    if (current.st_dev,current.st_ino)!=(inode.st_dev,inode.st_ino):raise ValueError('AGENT_SEED_CHANGED')
    sync_directory(path.parent)


def prepare_agent_runtime(root,release_id,*,failpoint=lambda phase:None):
    if os.geteuid()!=0:raise ValueError('AGENT_ROOT_PREPARATION_REQUIRED')
    root=Path(root).absolute()
    if release_policy(root,release_id)['agent']!='dedicated':raise ValueError('AGENT_RESTRICTED_POLICY_REQUIRED')
    identity=load_service_identity(root,'hue-agent');uid,gid=identity['uid'],identity['gid']
    paths=agent_runtime_paths(root,release_id);manifest=authenticated_manifest(root,release_id)
    config=json.loads(_master(root/'config/agent.json',8192),object_pairs_hook=unique_object)
    validate_agent_config(root,config)
    usb=json.loads(_master(root/'config/usb.json',4096),object_pairs_hook=unique_object)
    if not isinstance(usb,dict) or set(usb)!={'schema','vendor','product','serial'} or type(usb['schema']) is not int or usb['schema']!=1:
        raise ValueError('AGENT_USB_POLICY')
    transport=pinned_transport({key:usb[key] for key in ('vendor','product','serial')})
    _root_directory(paths['base'].parent,0o755)
    for path,mode in ((paths['base'],0o710),(paths['base']/'code',0o750),(paths['code'],0o750),
                      (paths['code']/'hue_agent',0o750),(paths['config_dir'],0o710)):
        _root_directory(path,mode,gid)
    expected=set()
    for name,entry in manifest['files'].items():
        if not name.startswith('agent/hue_agent/'):continue
        leaf=name.removeprefix('agent/hue_agent/')
        if not re.fullmatch(r'[a-z_]+\.py',leaf):raise ValueError('AGENT_CODE_PATH')
        data=private_bytes(root/'releases'/release_id/name,1024*1024)
        if len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
            raise ValueError('AGENT_CODE_HASH')
        target=paths['code']/'hue_agent'/leaf;projected_file(target,data,0o440,gid);expected.add(target)
    if not {paths['code']/'hue_agent/__init__.py',paths['code']/'hue_agent/__main__.py'}.issubset(expected):
        raise ValueError('AGENT_CODE_MISSING')
    for path in paths['code'].rglob('*'):
        if path.is_symlink() or path.is_file() and path not in expected or path.is_dir() and path!=paths['code']/'hue_agent':
            raise ValueError('AGENT_CODE_UNDECLARED')
    marker=_marker(root,identity)
    if marker is None:
        _no_links(paths['state_dir'])
        if paths['state_dir'].exists() and any(paths['state_dir'].iterdir()):raise ValueError('AGENT_RUNTIME_ORPHANED')
        for suffix in ('-wal','-shm','-journal'):
            if (root/('state/commands.sqlite'+suffix)).exists():raise ValueError('AGENT_CANONICAL_SIDECAR')
        bond=_master(root/'secrets/bond.json');journal=_master(root/'state/commands.sqlite',MAX_AGENT_FILE)
        if not isinstance(json.loads(bond),dict) or not json.loads(bond) or not journal.startswith(b'SQLite format 3\x00'):
            raise ValueError('AGENT_SEED_SOURCE')
        marker={'schema':1,'phase':'prepared','uid':uid,'gid':gid,
                'bondHash':hashlib.sha256(bond).hexdigest(),'journalHash':hashlib.sha256(journal).hexdigest()}
        atomic_replace(root/'state/agent-runtime.json',json.dumps(marker,sort_keys=True).encode());failpoint('prepared')
    _owned_directory(paths['state_dir'],uid,gid,required=marker['phase']=='ready')
    for path in (paths['run_dir'],paths['log_dir']):_owned_directory(path,uid,gid)
    if marker['phase'] in ('prepared','exported'):
        # PREPARED has never run; EXPORTED explicitly transferred authority to
        # canonical state after a stopped-agent checkpoint. A legacy rollback
        # can update that state before the next restricted start.
        if marker['phase']=='exported' and any((paths['state_dir']/('commands.sqlite'+s)).exists()
                                              or (paths['state_dir']/('commands.sqlite'+s)).is_symlink()
                                              for s in ('-wal','-shm','-journal')):
            # Canonical checkpoint is durable. Archive the whole old state
            # without traversing it, so its hot journal is never paired with
            # a newly seeded main database. A crash before seed retries here.
            import uuid
            _root_directory(root/'backups',0o700)
            paths['state_dir'].rename(root/'backups'/('sqlite-runtime-'+uuid.uuid4().hex))
            sync_directory(paths['state_dir'].parent);sync_directory(root/'backups')
            _owned_directory(paths['state_dir'],uid,gid)
        for suffix in ('-wal','-shm','-journal'):
            path=root/('state/commands.sqlite'+suffix)
            if path.exists() or path.is_symlink():raise ValueError('AGENT_CANONICAL_SIDECAR')
        bond=_master(root/'secrets/bond.json');journal=_master(root/'state/commands.sqlite',MAX_AGENT_FILE)
        if not isinstance(json.loads(bond),dict) or not json.loads(bond) or not journal.startswith(b'SQLite format 3\x00'):
            raise ValueError('AGENT_SEED_SOURCE')
        if hashlib.sha256(bond).hexdigest()!=marker['bondHash'] or hashlib.sha256(journal).hexdigest()!=marker['journalHash']:
            marker.update(bondHash=hashlib.sha256(bond).hexdigest(),journalHash=hashlib.sha256(journal).hexdigest())
            atomic_replace(root/'state/agent-runtime.json',json.dumps(marker,sort_keys=True).encode())
            failpoint('prepared:refresh')
        _seed_file(paths['keystore'],bond,uid,gid);failpoint('seed:bond')
        _seed_file(paths['journal'],journal,uid,gid);failpoint('seed:journal')
        marker['phase']='ready';atomic_replace(root/'state/agent-runtime.json',json.dumps(marker,sort_keys=True).encode());failpoint('ready')
    bond=read_agent_private(paths['keystore'],uid,gid)
    journal=read_agent_private(paths['journal'],uid,gid,MAX_AGENT_FILE)
    if not isinstance(json.loads(bond),dict) or not json.loads(bond) or not journal.startswith(b'SQLite format 3\x00'):
        raise ValueError('AGENT_RUNTIME_STATE')
    for source,target in ((root/'config/device.json',paths['device_config']),
                          (root/'secrets/api.token',paths['token_file']),(root/'secrets/device.token',paths['device_token'])):
        projected_file(target,_master(source),0o440,gid)
    config.update({name:str(paths[name]) for name in ('device_config','keystore','journal','token_file','lock_file','pid_file','log_file','health_socket')})
    config['transport']=transport
    config['tunnel']={**config['tunnel'],'token_file':str(paths['device_token'])}
    projected_file(paths['config'],json.dumps(config,sort_keys=True).encode(),0o440,gid)
    return identity,paths


def agent_environment(paths,release_id):
    return {'PATH':'/opt/bin:/opt/sbin:/usr/bin:/bin','HOME':str(paths['state_dir']),'LANG':'C',
            'PYTHONPATH':str(paths['code']),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1','HUE_RELEASE_ID':release_id}


def agent_process_matches(root,release_id,args,status,env,*,uid,gid,python):
    from .privileges import process_matches_identity
    paths=agent_runtime_paths(root,release_id)
    expected=[python,'-s','-P','-u','-m','hue_agent','--config',str(paths['config'])]
    if (not args or Path(args[0]).resolve()!=Path(python).resolve() or args[1:]!=expected[1:]
            or not process_matches_identity(status,uid,gid)):
        return False
    rows=dict(line.split(':',1) for line in status.splitlines() if ':' in line)
    if 'Groups' not in rows or rows['Groups'].strip():return False
    return env=={key.encode():value.encode() for key,value in agent_environment(paths,release_id).items()}


def checkpoint_agent_state(root,release_id):
    from .agent_snapshot import snapshot_agent_database
    root=Path(root).absolute()
    if os.geteuid()!=0 or child_pid('agent',root) is not None:raise ValueError('AGENT_CHECKPOINT_REQUIRES_STOP')
    from .service_manager import owner_record,supervisor_owned
    if supervisor_owned('agent',root,owner_record('agent',root)):
        raise ValueError('AGENT_CHECKPOINT_REQUIRES_STOP')
    if release_policy(root,release_id)['agent']!='dedicated':raise ValueError('AGENT_RESTRICTED_POLICY_REQUIRED')
    identity=load_service_identity(root,'hue-agent');marker=_marker(root,identity)
    if marker is None or marker['phase']!='ready':
        return {'checkpointed':False,'releaseId':release_id,'reason':'NO_ACTIVE_RUNTIME'}
    paths=agent_runtime_paths(root,release_id)
    bond=read_agent_private(paths['keystore'],identity['uid'],identity['gid'])
    if not isinstance(json.loads(bond),dict) or not json.loads(bond):raise ValueError('AGENT_CHECKPOINT_BOND')
    journal=snapshot_agent_database(paths['journal'],identity['uid'],identity['gid'],output_dir=root/'run',recover_hot=True)
    for suffix in ('-wal','-shm','-journal'):
        path=root/('state/commands.sqlite'+suffix)
        if path.exists() or path.is_symlink():
            _master(path,MAX_AGENT_FILE);path.unlink();sync_directory(path.parent)
    atomic_replace(root/'secrets/bond.json',bond)
    atomic_replace(root/'state/commands.sqlite',journal)
    # Only after BOTH canonical files are durable may legacy state become
    # authoritative. A crash earlier leaves READY and retries from runtime.
    marker.update(phase='exported',bondHash=hashlib.sha256(bond).hexdigest(),
                  journalHash=hashlib.sha256(journal).hexdigest())
    atomic_replace(root/'state/agent-runtime.json',json.dumps(marker,sort_keys=True).encode())
    return {'checkpointed':True,'releaseId':release_id}
