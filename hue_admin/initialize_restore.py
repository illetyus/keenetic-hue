"""Initialize an empty canonical installation from an authenticated private backup."""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
import uuid
from urllib.parse import urlsplit

from .activation import verify_slot,valid_id
from .installation import private_directory
from .manifest import MAX_BYTES,unique_object
from .migration import LegacyLayout,safe_path,rename
from .restore import decode_backup
from .restore_transaction import atomic_replace
from .service_manager import validate_agent_config
from .snapshot import layout_for,read_private_bytes


def names():
    _,sources=layout_for(Path('/unused'),True)
    return sorted(set(sources)|{'state/commands.sqlite'})


def targets():return [n for n in names() if n!='config/release-trust.pub']+['state/schema.json','active.json']


def receipt(root):
    path=safe_path(root/'state/initialize.json')
    if not path.exists():return None
    value=json.loads(read_private_bytes(path,8192),object_pairs_hook=unique_object)
    if (not isinstance(value,dict) or set(value)!={'schema','phase','releaseId','quarantine'}
            or type(value['schema']) is not int or value['schema']!=1
            or value['phase'] not in ('prepared','written','selected','started','committed','rolled_back')
            or not valid_id(value['releaseId']) or not isinstance(value['quarantine'],str)
            or not re.fullmatch('backups/initialize-[a-f0-9]{32}',value['quarantine'])):
        raise ValueError('INITIALIZE_RECEIPT')
    return value


def record(root,value,phase):
    value['phase']=phase;atomic_replace(root/'state/initialize.json',json.dumps(value,sort_keys=True).encode())


def recover_if_needed(root,service):
    root=Path(root).absolute();value=receipt(root)
    if value is None or value['phase'] in ('committed','rolled_back'):return {'pending':False}
    if not service('stop'):raise RuntimeError('INITIALIZE_STOP_FAILED')
    quarantine=root/value['quarantine']
    for folder in ('','config','secrets','state'):private_directory(quarantine/folder)
    # Preserve partially restored data, including any reservations made before failure.
    # Remove selection first; a subsequent recovery continues from the same receipt.
    for name in ['active.json',*targets()[:-1],'state/commands.sqlite-wal','state/commands.sqlite-shm']:
        path=safe_path(root/name);saved=safe_path(quarantine/name)
        if path.exists():
            if not path.is_file():raise ValueError('INITIALIZE_TARGET_TYPE')
            rename(path,saved)
    record(root,value,'rolled_back')
    return {'pending':False,'initializationRolledBack':True}


def validate_files(root,files,identity,key,*,release_id=None):
    if set(files)!=set(names()):raise ValueError('INITIALIZE_FILE_SET')
    if files['config/release-trust.pub']!=key or read_private_bytes(root/'config/release-trust.pub',32)!=key:
        raise ValueError('INITIALIZE_TRUST_KEY')
    cfg=json.loads(files['config/agent.json'],object_pairs_hook=unique_object)
    validate_agent_config(root,cfg)
    if identity!={'adapter':cfg['controller_public_address'],'lamp':cfg['address']}:raise ValueError('INITIALIZE_IDENTITY')
    device=json.loads(files['config/device.json'],object_pairs_hook=unique_object)
    bond=json.loads(files['secrets/bond.json'],object_pairs_hook=unique_object)
    if not isinstance(device,dict) or not isinstance(bond,dict) or not bond:raise ValueError('INITIALIZE_BLE_DATA')
    management=json.loads(files['config/management.json'],object_pairs_hook=unique_object)
    if not isinstance(management,dict) or set(management)!={'publicOrigin'}:raise ValueError('INITIALIZE_MANAGEMENT')
    origin=urlsplit(management['publicOrigin'])
    if (origin.scheme!='https' or not origin.hostname or origin.username or origin.password or origin.port not in (None,443)
            or origin.path not in ('','/') or origin.query or origin.fragment):raise ValueError('INITIALIZE_ORIGIN')
    for name in ('api','device','tunnel','setup-issuer'):
        token=files['secrets/'+name+'.token'].decode('ascii').strip()
        if not 32<=len(token)<=4096 or any(c.isspace() for c in token):raise ValueError('INITIALIZE_TOKEN')
    if release_id is not None:
        from .runtime_policy import release_policy
        if release_policy(root,release_id)['agent']=='dedicated':
            from .agent_snapshot import validate_agent_database_bytes
            validate_agent_database_bytes(root,release_id,files['state/commands.sqlite'])
            return
    with tempfile.TemporaryDirectory(prefix='hue-restore-db-check-') as tmp:
        path=Path(tmp)/'journal';path.write_bytes(files['state/commands.sqlite'])
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            deadline=time.monotonic()+5
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            db.execute('pragma trusted_schema=OFF')
            if db.execute('pragma quick_check').fetchone()!=('ok',):raise ValueError('INITIALIZE_DATABASE')
            # Match the exact application schema, not merely a valid SQLite container.
            with closing(sqlite3.connect(':memory:')) as expected:
                expected.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
                expected.execute('CREATE TABLE sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)')
                query='select type,name,tbl_name,sql from sqlite_master order by type,name'
                if db.execute(query).fetchall()!=expected.execute(query).fetchall():raise ValueError('INITIALIZE_DATABASE_SCHEMA')


def initialize(root,archive,password,release_id,key,runtime,service,health,dry_run=False,*,legacy=LegacyLayout(),failpoint=lambda phase:None):
    """Caller owns management_lock; selected software is independently signed."""
    root=Path(root).absolute();safe_path(root)
    pending=receipt(root)
    if pending and pending['phase'] not in ('committed','rolled_back'):raise RuntimeError('INITIALIZE_RECOVERY_REQUIRED')
    if any(safe_path(root/name).exists() for name in targets()):raise ValueError('INITIALIZE_REQUIRES_EMPTY_INSTALLATION')
    if any(safe_path(p).exists() for p in (*legacy.directories().values(),*legacy.entries().values())):
        raise ValueError('INITIALIZE_LEGACY_INSTALLATION_PRESENT')
    slot=verify_slot(root,release_id,key,runtime)
    if json.loads(read_private_bytes(slot/'metadata/state-schema.json',4096))!={'schema':1,'read':[1],'write':1}:
        raise ValueError('INITIALIZE_RELEASE_SCHEMA')
    files,identity=decode_backup(read_private_bytes(archive,MAX_BYTES),password)
    validate_files(root,files,identity,key,release_id=release_id)
    if dry_run:return {'verified':True,'files':len(files),'installationChanged':False,'blankInstallation':True}
    for name in ('config','secrets','state','run','logs','backups'):private_directory(root/name)
    value={'schema':1,'phase':'prepared','releaseId':release_id,'quarantine':'backups/initialize-'+uuid.uuid4().hex}
    record(root,value,'prepared');failpoint('prepared')
    try:
        for name,data in files.items():
            if name=='config/release-trust.pub':continue
            atomic_replace(root/name,data);failpoint('write:'+name)
        record(root,value,'written');failpoint('written')
        atomic_replace(root/'state/schema.json',b'{"schema":1}')
        atomic_replace(root/'active.json',json.dumps({'schema':1,'releaseId':release_id}).encode())
        record(root,value,'selected');failpoint('selected')
        if not service('start'):raise RuntimeError('INITIALIZE_START_FAILED')
        record(root,value,'started');failpoint('started')
        observed=health()
        if observed.get('healthy') is not True or observed.get('releaseId')!=release_id:raise RuntimeError('INITIALIZE_HEALTH_FAILED')
        record(root,value,'committed')
        return {'initialized':True,'files':len(files),'releaseId':release_id,'healthVerified':True}
    except Exception:
        recover_if_needed(root,service);raise
