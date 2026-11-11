"""Orchestrate authenticated release maintenance without changing Entware packages."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import zipfile

from .activation import activate_candidate,rollback,recover_activation,pointer,release_order
from .installation import install_candidate,recover_installation,installation_receipt
from .platform import runtime_for
from .releases import verify_signed_manifest,verify_release,open_archive,MAX_MANIFEST,preflight_zip,bounded_manifest_read
from .restore_current import management_lock,recover_current
from .snapshot import SYSTEM,read_private_bytes


def trusted_key(root,trust_file=None,trust_pin=None):
    anchor=root/'config/release-trust.pub'
    if trust_file is None:return read_private_bytes(anchor,32)
    if not isinstance(trust_pin,str) or not re.fullmatch('[a-fA-F0-9]{64}',trust_pin):raise ValueError('TRUST_PIN_REQUIRED')
    path=Path(trust_file).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('TRUST_KEY_SYMLINK')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('TRUST_KEY_TYPE')
        key=os.read(fd,33)
    finally:os.close(fd)
    if len(key)!=32 or hashlib.sha256(key).hexdigest()!=trust_pin.lower():raise ValueError('TRUST_PIN_MISMATCH')
    if anchor.exists() and read_private_bytes(anchor,32)!=key:raise ValueError('TRUST_KEY_ROTATION_REQUIRES_SEPARATE_PROCEDURE')
    return key


def runtime_from_manifest(raw,key):
    value=json.loads(raw)
    manifest=verify_signed_manifest(raw,key,value.get('architecture'),value.get('pythonAbi'),value.get('dependencies'))
    runtime=runtime_for(manifest['dependencies'])
    verify_signed_manifest(raw,key,*runtime)
    return runtime


def archive_release(path,key):
    with open_archive(path) as source:
        preflight_zip(source)
        with zipfile.ZipFile(source) as archive:
            raw=bounded_manifest_read(archive)
    runtime=runtime_from_manifest(raw,key)
    return verify_release(path,key,*runtime),runtime


def runtime_for_root(root,key):
    install=installation_receipt(root)
    if install and install['phase']!='committed':
        staged=root/'releases'/install['stage']/'verified-manifest.json'
        if staged.is_file() and not staged.is_symlink():return runtime_from_manifest(read_private_bytes(staged,MAX_MANIFEST),key)
    selected=pointer(root)
    if selected:return runtime_from_manifest(read_private_bytes(root/'releases'/selected['releaseId']/'verified-manifest.json',MAX_MANIFEST),key)
    raise ValueError('NO_VERIFIED_RUNTIME_CONTEXT')


def recover_locked(root,service):
    from .bootstrap_install import finish_if_needed
    finish_if_needed(root)
    from .initialize_restore import recover_if_needed as recover_initialization
    from .migration import new_action
    recover_initialization(root,lambda verb:new_action(verb,root))
    # Migration has its own legacy-owner receipt; it must settle before new-root recovery.
    migration=root/'state/migration.json'
    if migration.exists():
        from .migration import recover_if_needed
        recover_if_needed(root)
    restored=False
    pending=root/'state/restore-pending/receipt.json'
    if pending.is_symlink():raise ValueError('RESTORE_SYMLINK')
    if pending.exists():
        value=json.loads(read_private_bytes(pending,32768))
        if value.get('phase') not in ('done','rolled_back'):
            recover_current(service,locked=True,root=root);restored=True
    if not (root/'active.json').exists() and not (root/'state/schema.json').exists():
        return {'configured':False,'restoreRecovered':restored}
    key=trusted_key(root);runtime=runtime_for_root(root,key)
    stop=lambda:all([service('tunnel','stop'),service('agent','stop')])
    start=lambda:service('agent','start') and service('tunnel','start')
    installed=recover_installation(root,key,runtime,stop)
    if installed.get('pending') and not installed.get('activeUnaffected'):raise RuntimeError('RECOVERY_REQUIRES_RELEASE_ARCHIVE')
    from .runtime_repair import recover_if_needed as recover_runtime
    runtime_recovered=recover_runtime(root)
    recover_activation(root,key,runtime,stop,start)
    if runtime_recovered and not start():raise RuntimeError('RUNTIME_REPAIR_START_FAILED')
    return {'configured':True,'recoveryComplete':True,'restoreRecovered':restored,'runtimeRecovered':runtime_recovered}


def execute(operation,archive=None,trust_file=None,trust_pin=None,boot_action=None,*,root=SYSTEM,service,health):
    if operation not in ('install','update','repair','rollback','recover','internal-service'):raise ValueError('RELEASE_OPERATION')
    root=Path(root).absolute()
    with management_lock(root):
        stop=lambda:all([service('tunnel','stop'),service('agent','stop')])
        start=lambda:service('agent','start') and service('tunnel','start')
        pending_runtime=None
        if operation in ('install','update','repair','rollback'):
            from .runtime_repair import receipt as runtime_receipt
            pending_runtime=runtime_receipt(root)
            if pending_runtime and pending_runtime['phase']=='prepared' and operation!='repair':
                raise RuntimeError('RUNTIME_REPAIR_RECOVERY_REQUIRED')
        def healthy_version(expected):
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                observed=health()
                if observed.get('healthy') is True and observed.get('releaseId')==expected:return observed
                time.sleep(1)
            return {'healthy':False,'releaseId':None}
        if operation in ('recover','internal-service'):
            if operation=='internal-service' and boot_action not in ('start','stop','restart','status'):raise ValueError('BOOT_ACTION')
            if operation=='internal-service' and boot_action=='stop':
                if not stop():raise RuntimeError('BOOT_STOP_FAILED')
                return {'stopped':True}
            if operation=='internal-service' and boot_action=='status':return health()
            recovered=recover_locked(root,service)
            if operation=='recover' or not recovered['configured']:return recovered
            if boot_action=='restart' and not stop():raise RuntimeError('BOOT_STOP_FAILED')
            if not start():raise RuntimeError('BOOT_START_FAILED')
            return {'configured':True,'ownersStarted':True,'physicalHealthRequiresLiveCheck':True}
        key=trusted_key(root,trust_file,trust_pin)
        if operation=='rollback':
            selected=pointer(root)
            if not selected or 'previousReleaseId' not in selected:raise ValueError('ROLLBACK_NO_PREVIOUS_RELEASE')
            previous=selected['previousReleaseId']
            raw=read_private_bytes(root/'releases'/previous/'verified-manifest.json',MAX_MANIFEST)
            runtime=runtime_from_manifest(raw,key)
            return rollback(root,key,runtime,stop,start,lambda:healthy_version(previous))
        release,runtime=archive_release(archive,key)
        release_id=json.loads(release.signed_manifest)['releaseId']
        if pending_runtime and pending_runtime['phase']=='prepared' and pending_runtime['releaseId']!=release_id:
            raise RuntimeError('RUNTIME_REPAIR_RECOVERY_REQUIRED')
        if operation=='update':
            selected=pointer(root)
            if selected:
                release_order(root,read_private_bytes(root/'releases'/selected['releaseId']/'verified-manifest.json',MAX_MANIFEST),key,runtime,True)
            release_order(root,release.signed_manifest,key,runtime)
        result=install_candidate(release,root,'fresh' if operation=='install' else 'repair',stop_active=stop if operation=='repair' else None)
        if operation=='install':return result
        if operation=='repair':
            selected=pointer(root)
            if selected and selected['releaseId']==release_id:
                from .runtime_repair import repair_runtime
                if not stop():raise RuntimeError('REPAIR_STOP_FAILED')
                result.update(repair_runtime(root,release_id))
                if not start() or not healthy_version(release_id)['healthy']:raise RuntimeError('REPAIR_HEALTH_FAILED')
                result['versionHealthVerified']=True
            return result
        selected=pointer(root)
        from .bootstrap_install import bootstrap_sync_status
        sync=bootstrap_sync_status(root,release_id)
        if selected and selected['releaseId']==release_id and result['alreadyVerified']:
            if not start() or not healthy_version(release_id)['healthy']:raise RuntimeError('UPDATE_CURRENT_HEALTH_FAILED')
            return {'activated':False,'alreadyVerified':True,'releaseId':release_id,'versionHealthVerified':True,
                    'bootstrapSyncRequired':not sync['bootstrapInSync'],'bootstrapReleaseId':sync['bootstrapReleaseId']}
        outcome=activate_candidate(root,release_id,key,runtime,stop,start,lambda:healthy_version(release_id))
        outcome.update(bootstrapSyncRequired=not sync['bootstrapInSync'],bootstrapReleaseId=sync['bootstrapReleaseId'])
        return outcome
