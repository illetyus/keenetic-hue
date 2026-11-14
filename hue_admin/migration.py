"""One-time, recoverable migration of the fixed legacy installation.

Pending migrations roll back on boot. No reset, pairing or lamp write is issued.
Legacy software and init entries are renamed into a private backup, never deleted.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from .activation import verify_slot, valid_id
from .installation import private_directory
from .manifest import unique_object
from .restore_transaction import atomic_replace, sync_directory
from .service_manager import agent_paths, validate_agent_config
from .snapshot import read_private_bytes, sqlite_snapshot


@dataclass(frozen=True)
class LegacyLayout:
    agent: Path=Path('/opt/var/lib/hue-agent')
    tunnel: Path=Path('/opt/var/lib/hue-tunnel')
    ble: Path=Path('/opt/var/lib/keenetic-hue')
    agent_init: Path=Path('/opt/etc/init.d/S99hue-agent')
    tunnel_init: Path=Path('/opt/etc/init.d/S98hue-tunnel')

    def directories(self):return {'agent':self.agent,'tunnel':self.tunnel,'ble':self.ble}
    def entries(self):return {'agent':self.agent_init,'tunnel':self.tunnel_init}
    def sources(self):
        return {'config/agent.json':self.agent/'config.json','config/device.json':self.ble/'device.json',
                'secrets/bond.json':self.ble/'keystore.json','secrets/api.token':self.agent/'api.token',
                'secrets/device.token':self.agent/'cloud.token','secrets/tunnel.token':self.tunnel/'tunnel.token'}


FILES=('config/agent.json','config/device.json','secrets/bond.json','secrets/api.token',
       'secrets/device.token','secrets/tunnel.token','state/commands.sqlite','state/schema.json','active.json')
PHASES=('prepared','disabled','stopped','copied','selected','started','verified','committed','rolled_back')


def digest(data):return hashlib.sha256(data).hexdigest()


def safe_path(path):
    path=Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('MIGRATION_SYMLINK')
    return path


def read_receipt(root):
    path=safe_path(root/'state/migration.json')
    if not path.exists():return None
    value=json.loads(read_private_bytes(path,32768),object_pairs_hook=unique_object)
    if (not isinstance(value,dict) or set(value)!={'schema','phase','releaseId','backup','before','entries'}
            or type(value['schema']) is not int or value['schema']!=1 or value['phase'] not in PHASES
            or not valid_id(value['releaseId']) or not isinstance(value['backup'],str)
            or not re.fullmatch('backups/migration-[a-f0-9]{32}',value['backup'])
            or not isinstance(value['before'],dict) or set(value['before'])!=set(FILES)
            or not isinstance(value['entries'],dict) or set(value['entries'])!={'agent','tunnel'}
            or any(v is not None and (not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v)) for v in value['before'].values())
            or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in value['entries'].values())):
        raise ValueError('MIGRATION_RECEIPT')
    safe_path(root/value['backup'])
    return value


def record(root,value,phase):
    value['phase']=phase;atomic_replace(root/'state/migration.json',json.dumps(value,sort_keys=True).encode())


def rename(source,target):
    safe_path(source);safe_path(target)
    if target.exists():raise ValueError('MIGRATION_RENAME_COLLISION')
    source.rename(target);sync_directory(source.parent);sync_directory(target.parent)


def legacy_action(verb,root,layout=LegacyLayout()):
    if verb not in ('start','stop'):return False
    receipt=read_receipt(root);results=[]
    names=('agent','tunnel') if verb=='start' else ('tunnel','agent')
    for name in names:
        path=layout.entries()[name]
        if not path.exists() and receipt:path=root/receipt['backup']/'entries'/name
        try:
            safe_path(path)
            if not path.is_file():raise ValueError('MIGRATION_LEGACY_ENTRY')
            if receipt and digest(read_private_bytes(path,65536))!=receipt['entries'][name]:raise ValueError('MIGRATION_LEGACY_ENTRY_HASH')
            results.append(subprocess.run(['/opt/bin/sh',str(path),verb],stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=55).returncode==0)
        except (OSError,ValueError,subprocess.SubprocessError):results.append(False)
    return all(results)


def new_action(verb,root):
    from .service_manager import action
    names=('agent','tunnel') if verb=='start' else ('tunnel','agent')
    return all([action(name,verb,root) for name in names])


def validate_legacy(root,layout):
    for path in (*layout.directories().values(),*layout.entries().values()):safe_path(path)
    cfg=json.loads(read_private_bytes(layout.agent/'config.json',8192),object_pairs_hook=unique_object)
    expected={'device_config':str(layout.ble/'device.json'),'keystore':str(layout.ble/'keystore.json'),
              'journal':str(layout.agent/'commands.sqlite'),'token_file':str(layout.agent/'api.token')}
    if any(cfg.get(k)!=v for k,v in expected.items()):raise ValueError('MIGRATION_LEGACY_LAYOUT')
    allowed={'lock_file':(layout.agent/'agent.lock',layout.ble/'agent.lock'),
             'log_file':(layout.agent/'agent.log',),'pid_file':(layout.agent/'agent.pid',)}
    for field in allowed:
        path=Path(cfg.get(field,''))
        if path not in allowed[field]:raise ValueError('MIGRATION_LEGACY_PATH')
        safe_path(path)
    if cfg.get('port')!=8787 or cfg.get('cloud')!={'enabled':False} or cfg.get('tunnel')!={'enabled':True,'port':8788,'token_file':str(layout.agent/'cloud.token')}:
        raise ValueError('MIGRATION_LEGACY_MODE')
    converted=agent_paths(root)
    converted.update({k:cfg[k] for k in ('transport','address','controller_public_address','port','cloud')})
    converted['tunnel']={'enabled':True,'port':8788,'token_file':str(root/'secrets/device.token')}
    validate_agent_config(root,converted)
    for path in layout.sources().values():read_private_bytes(path)
    for name in ('config/management.json','secrets/setup-issuer.token','config/release-trust.pub'):read_private_bytes(root/name)
    return cfg,converted


def confirm_adapter_free(path):
    if os.name!='posix':return
    import fcntl
    fd=os.open(safe_path(path),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        import stat
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('MIGRATION_LOCK_TYPE')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:os.close(fd)


def rollback(root,value,old,new,layout):
    backup=root/value['backup']
    # Authenticate every recovery input before signalling or writing anything.
    before={name:read_private_bytes(backup/'before'/name) for name,h in value['before'].items() if h is not None}
    if any(digest(data)!=value['before'][name] for name,data in before.items()):raise ValueError('MIGRATION_BACKUP_HASH')
    for name,path in layout.entries().items():
        saved=backup/'entries'/name
        existing=path if path.exists() else saved
        if digest(read_private_bytes(existing,65536))!=value['entries'][name]:raise ValueError('MIGRATION_ENTRY_HASH')
    if not new('stop'):raise RuntimeError('MIGRATION_NEW_STOP_FAILED')
    # Preserve reservations processed by the new agent; never replay an older journal.
    journal=None
    if value['phase'] in ('selected','started','verified') and (root/'state/commands.sqlite').exists():
        journal=sqlite_snapshot(root/'state/commands.sqlite')
    for name,path in layout.directories().items():
        saved=backup/'legacy'/name
        safe_path(path);safe_path(saved)
        if saved.exists():
            if path.exists():raise ValueError('MIGRATION_DIRECTORY_COLLISION')
            rename(saved,path)
    if journal is not None:
        # Both owners stopped: remove obsolete WAL/journal sidecars before restore.
        for suffix in ('-wal','-shm','-journal'):
            side=safe_path(layout.agent/('commands.sqlite'+suffix))
            if side.exists():
                if not side.is_file():raise ValueError('MIGRATION_DATABASE_TYPE')
                side.unlink();sync_directory(side.parent)
        atomic_replace(layout.agent/'commands.sqlite',journal)
    for suffix in ('-wal','-shm','-journal'):
        side=safe_path(root/('state/commands.sqlite'+suffix))
        if side.exists():
            if not side.is_file():raise ValueError('MIGRATION_TARGET_TYPE')
            side.unlink();sync_directory(side.parent)
    for name in FILES:
        path=safe_path(root/name)
        if name in before:atomic_replace(path,before[name])
        elif path.exists():
            if not path.is_file():raise ValueError('MIGRATION_TARGET_TYPE')
            path.unlink();sync_directory(path.parent)
    for name,path in layout.entries().items():
        saved=backup/'entries'/name
        if not path.exists():rename(saved,path)
    if not old('start'):raise RuntimeError('MIGRATION_LEGACY_RESTART_FAILED')
    record(root,value,'rolled_back')


def recover_if_needed(root,old=None,new=None,*,layout=LegacyLayout()):
    root=Path(root).absolute();value=read_receipt(root)
    if value is None or value['phase'] in ('committed','rolled_back'):return {'pending':False}
    old=old or (lambda verb:legacy_action(verb,root,layout));new=new or (lambda verb:new_action(verb,root))
    rollback(root,value,old,new,layout)
    return {'pending':False,'migrationRolledBack':True}


def migrate(root,release_id,key,runtime,old=None,new=None,health=None,*,layout=LegacyLayout(),failpoint=lambda phase:None):
    """Caller holds management_lock. This function never accepts CLI-supplied roots."""
    root=Path(root).absolute();safe_path(root)
    existing=read_receipt(root)
    if existing and existing['phase']=='committed':return {'alreadyMigrated':True,'releaseId':existing['releaseId']}
    if existing and existing['phase']!='rolled_back':raise RuntimeError('MIGRATION_RECOVERY_REQUIRED')
    if (root/'active.json').exists() or (root/'state/schema.json').exists():raise ValueError('MIGRATION_ALREADY_CONFIGURED')
    slot=verify_slot(root,release_id,key,runtime)
    schema=json.loads(read_private_bytes(slot/'metadata/state-schema.json',4096),object_pairs_hook=unique_object)
    if schema!={'schema':1,'read':[1],'write':1}:raise ValueError('MIGRATION_STATE_SCHEMA')
    cfg,converted=validate_legacy(root,layout)
    old=old or (lambda verb:legacy_action(verb,root,layout));new=new or (lambda verb:new_action(verb,root))
    if health is None:
        from .unified_health import status
        health=lambda:status(True,root)
    for folder in ('state','config','secrets','run','logs','backups'):private_directory(root/folder)
    backup_name='backups/migration-'+uuid.uuid4().hex;backup=root/backup_name;private_directory(backup)
    for folder in ('before','before/config','before/secrets','before/state','entries','legacy'):private_directory(backup/folder)
    before={name:read_private_bytes(root/name) if safe_path(root/name).exists() else None for name in FILES}
    entries={name:read_private_bytes(path,65536) for name,path in layout.entries().items()}
    for name,data in before.items():
        if data is not None:atomic_replace(backup/'before'/name,data)
    # Hash and save entry content before recording intent to disable it.
    for name,data in entries.items():atomic_replace(backup/'entries'/name,data)
    value={'schema':1,'phase':'prepared','releaseId':release_id,'backup':backup_name,
           'before':{n:digest(d) if d is not None else None for n,d in before.items()},
           'entries':{n:digest(d) for n,d in entries.items()}}
    record(root,value,'prepared');failpoint('prepared')
    try:
        for name,path in layout.entries().items():
            # Backup already durable; rename the executable itself to retain its mode.
            saved=backup/'entries'/name;saved.unlink();sync_directory(saved.parent)
            rename(path,saved);failpoint('disabled:'+name)
        record(root,value,'disabled')
        if not old('stop'):raise RuntimeError('MIGRATION_LEGACY_STOP_FAILED')
        confirm_adapter_free(Path(cfg['lock_file']))
        record(root,value,'stopped');failpoint('stopped')
        files={n:read_private_bytes(p) for n,p in layout.sources().items()}
        if json.loads(files['config/agent.json'],object_pairs_hook=unique_object)!=cfg:
            raise ValueError('MIGRATION_SOURCE_CONFIG_CHANGED')
        files['config/agent.json']=json.dumps(converted,sort_keys=True).encode()
        files['state/commands.sqlite']=sqlite_snapshot(layout.agent/'commands.sqlite')
        files['state/schema.json']=b'{"schema":1}'
        for name,data in files.items():atomic_replace(root/name,data);failpoint('copied:'+name)
        record(root,value,'copied');failpoint('copied')
        atomic_replace(root/'active.json',json.dumps({'schema':1,'releaseId':release_id}).encode())
        record(root,value,'selected');failpoint('selected')
        if not new('start'):raise RuntimeError('MIGRATION_NEW_START_FAILED')
        record(root,value,'started');failpoint('started')
        observed=health()
        if observed.get('healthy') is not True or observed.get('releaseId')!=release_id:raise RuntimeError('MIGRATION_HEALTH_FAILED')
        record(root,value,'verified');failpoint('verified')
        for name,path in layout.directories().items():rename(path,backup/'legacy'/name);failpoint('archived:'+name)
        record(root,value,'committed')
        return {'migrated':True,'releaseId':release_id,'backup':backup_name,'healthVerified':True}
    except Exception:
        rollback(root,value,old,new,layout);raise
