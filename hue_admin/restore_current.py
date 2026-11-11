"""Same-device restore for explicit legacy or unified layouts."""
from contextlib import closing, contextmanager
import json
import os
import sqlite3
import stat
import tempfile
import time
from pathlib import Path

from .archive_commands import verify_backup
from .manifest import unique_object
from .restore_transaction import apply_files, recover
from .snapshot import SYSTEM, layout_for, read_private_bytes, validate_layout

def targets(root=None):
    layout,sources=layout_for(SYSTEM if root is None else root)
    return {**sources,'state/commands.sqlite':Path(layout['journal'])}


@contextmanager
def management_lock(root=None):
    import fcntl
    root=SYSTEM if root is None else Path(root)
    root=root.absolute()
    path=root/'management.lock'
    if root.is_symlink() or any(p.is_symlink() for p in root.parents) or path.is_symlink():
        raise ValueError('MANAGEMENT_LOCK_SYMLINK')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0),0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077:raise ValueError('MANAGEMENT_LOCK_FILE')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('MANAGEMENT_BUSY') from None
        yield
    finally:os.close(fd)


def current_identity():
    _,sources=layout_for(SYSTEM)
    config=json.loads(read_private_bytes(sources['config/agent.json'],8192))
    validate_layout(config,SYSTEM)
    return {'adapter':config['controller_public_address'],'lamp':config['address']}


def restricted_release(root):
    from .service_manager import selection
    from .runtime_policy import release_policy
    root=Path(root)
    if not (root/'active.json').exists() and not (root/'active.json').is_symlink():return None
    release_id=selection(root)
    return release_id if release_policy(root,release_id)['agent']=='dedicated' else None


def validate_files(files, identity):
    layout,sources=layout_for(SYSTEM)
    if set(files)!=set(targets()):raise ValueError('RESTORE_FILE_SET')
    config=json.loads(files['config/agent.json'],object_pairs_hook=unique_object)
    current=json.loads(read_private_bytes(sources['config/agent.json'],8192))
    validate_layout(config,SYSTEM)
    if config!=current:
        # First delivery restores credentials/state only to exactly the same layout/config.
        # Migration to new root or changed ports is a separate verified operation.
        raise ValueError('RESTORE_CONFIG_DIFFERS_USE_MIGRATION')
    if identity!={'adapter':config['controller_public_address'],'lamp':config['address']}:
        raise ValueError('RESTORE_IDENTITY')
    device=json.loads(files['config/device.json'],object_pairs_hook=unique_object)
    if device!=json.loads(read_private_bytes(sources['config/device.json'],8192)):
        raise ValueError('RESTORE_DEVICE_CONFIG_DIFFERS')
    bond=json.loads(files['secrets/bond.json'],object_pairs_hook=unique_object)
    if not isinstance(bond,dict) or not bond:raise ValueError('RESTORE_BOND')
    management=json.loads(files['config/management.json'],object_pairs_hook=unique_object)
    if management!=json.loads(read_private_bytes(sources['config/management.json'],8192)):
        raise ValueError('RESTORE_CLOUD_ORIGIN_DIFFERS')
    if 'config/release-trust.pub' in sources and files['config/release-trust.pub']!=read_private_bytes(sources['config/release-trust.pub'],32):
        raise ValueError('RESTORE_TRUST_KEY_DIFFERS')
    for name in ('api.token','device.token','tunnel.token','setup-issuer.token'):
        token=files['secrets/'+name].decode('ascii').strip()
        if not 32<=len(token)<=4096 or any(c.isspace() for c in token):
            raise ValueError('RESTORE_TOKEN')
    release_id=restricted_release(SYSTEM)
    if release_id:
        from .agent_snapshot import validate_agent_database_bytes
        validate_agent_database_bytes(SYSTEM,release_id,files['state/commands.sqlite'])
        return
    with tempfile.TemporaryDirectory(prefix='hue-restore-check-') as tmp:
        path=Path(tmp)/'journal.sqlite'
        fd=os.open(path,os.O_CREAT|os.O_WRONLY|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as out:out.write(files['state/commands.sqlite'])
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            db.execute('pragma trusted_schema=OFF')
            db.execute('pragma query_only=ON')
            # Entware SQLite omits extension-loading support entirely.
            disable_extensions=getattr(db,'enable_load_extension',None)
            if disable_extensions is not None:disable_extensions(False)
            deadline=time.monotonic()+5
            db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            if db.execute('pragma quick_check').fetchone()!=('ok',):raise ValueError('RESTORE_DATABASE')
            schema=db.execute("select type,name,tbl_name,sql from sqlite_master order by type,name").fetchall()
            with closing(sqlite3.connect(Path(layout['journal']).as_uri()+'?mode=ro',uri=True,timeout=2)) as live:
                live.execute('pragma trusted_schema=OFF')
                live.execute('pragma query_only=ON')
                if schema!=live.execute("select type,name,tbl_name,sql from sqlite_master order by type,name").fetchall():
                    raise ValueError('RESTORE_DATABASE_SCHEMA')


def checkpoint():
    release_id=restricted_release(SYSTEM)
    if release_id:
        from .agent_runtime import _marker
        from .privileges import load_service_identity
        marker=_marker(SYSTEM,load_service_identity(SYSTEM,'hue-agent'))
        if marker is None or marker['phase']!='exported':raise ValueError('RESTORE_AGENT_EXPORT_REQUIRED')
        for suffix in ('-wal','-shm','-journal'):
            path=SYSTEM/('state/commands.sqlite'+suffix)
            if path.exists() or path.is_symlink():raise ValueError('RESTORE_CANONICAL_SIDECAR')
        return
    layout,_=layout_for(SYSTEM)
    with closing(sqlite3.connect(layout['journal'],timeout=2)) as db:
        result=db.execute('pragma wal_checkpoint(TRUNCATE)').fetchone()
        if result and result[0]!=0:raise RuntimeError('RESTORE_DATABASE_BUSY')


def restore_from(path, password, dry_run, service, health):
    with management_lock():
        return _restore_from(path,password,dry_run,service,health)


def _restore_from(path, password, dry_run, service, health):
    from .runtime_repair import receipt as runtime_receipt
    pending_runtime=runtime_receipt(SYSTEM)
    if pending_runtime and pending_runtime['phase']=='prepared':raise RuntimeError('RUNTIME_REPAIR_RECOVERY_REQUIRED')
    transaction=SYSTEM/'state/restore-pending'
    identity=current_identity()
    files=verify_backup(path,password,identity)
    validate_files(files,identity)
    if dry_run:return {'verified':True,'files':len(files),'installationChanged':False}
    state=SYSTEM/'state'
    if state.is_symlink():raise ValueError('RESTORE_SYMLINK')
    state.mkdir(mode=0o700,exist_ok=True)
    if transaction.exists():
        receipt=json.loads(read_private_bytes(transaction/'receipt.json',32768))
        if receipt.get('phase') not in ('done','rolled_back'):
            raise RuntimeError('RESTORE_RECOVERY_REQUIRED')
        transaction.rename(state/('restore-complete-'+str(time.time_ns())))
    def stop():return all([service('tunnel','stop'),service('agent','stop')])
    def start():return service('agent','start') and service('tunnel','start')
    def healthy():
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            if health()['healthy']:return True
            time.sleep(1)
        return False
    return apply_files(files,targets(),transaction,stop,start,healthy,checkpoint=checkpoint)


def recover_current(service,locked=False,root=None):
    root=SYSTEM if root is None else Path(root)
    def perform():
        recover(targets(root),root/'state/restore-pending',lambda:all([service('tunnel','stop'),service('agent','stop')]),
                lambda:service('agent','start') and service('tunnel','start'))
    if locked:perform()
    else:
        with management_lock(root):perform()
    return {'recoveryComplete':True}
