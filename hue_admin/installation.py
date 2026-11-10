"""Install an inactive, verified release slot without touching credentials."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from .manifest import unique_object
from .releases import extract_release, digest_stream, verify_signed_manifest
from .restore_transaction import atomic_replace, sync_directory
from .snapshot import read_private_bytes


def private_directory(path):
    path=Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('INSTALL_SYMLINK')
    path.mkdir(mode=0o700,exist_ok=True)
    info=path.stat()
    if not stat.S_ISDIR(info.st_mode) or os.name=='posix' and (stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=os.geteuid()):
        raise ValueError('INSTALL_PRIVATE_DIRECTORY')


@contextmanager
def install_lock(root):
    path=root/'installation.lock'
    if path.is_symlink():raise ValueError('INSTALL_SYMLINK')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        if os.name=='posix':
            import fcntl
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        else:
            import msvcrt
            if os.fstat(fd).st_size==0:os.write(fd,b'\0')
            os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
        yield
    finally:os.close(fd)


def active_release(root):
    path=root/'active.json'
    if path.is_symlink():raise ValueError('INSTALL_ACTIVE_POINTER')
    if not path.exists():return None
    value=json.loads(read_private_bytes(path,4096),object_pairs_hook=unique_object)
    if (not isinstance(value,dict) or type(value.get('schema')) is not int or value['schema']!=1
            or set(value)-{'schema','releaseId','previousReleaseId'}
            or not isinstance(value.get('releaseId'),str)
            or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',value['releaseId'])):
        raise ValueError('INSTALL_ACTIVE_POINTER')
    return value['releaseId']


def slot_verified(slot, signed_manifest):
    try:
        if slot.is_symlink() or not slot.is_dir():return False
        manifest=json.loads(signed_manifest)
        expected=manifest['files']
        actual=[]
        for path in slot.rglob('*'):
            if path.is_symlink():return False
            if path.is_file():actual.append(path.relative_to(slot).as_posix())
            elif not path.is_dir():return False
            elif os.name=='posix' and stat.S_IMODE(path.stat().st_mode)!=0o700:return False
        if set(actual)!={*expected,'verified-manifest.json'}:return False
        if read_private_bytes(slot/'verified-manifest.json',128*1024)!=signed_manifest:return False
        for name,entry in expected.items():
            path=slot/name
            if os.name=='posix' and stat.S_IMODE(path.stat().st_mode)!=entry['mode']:return False
            with path.open('rb') as stream:digest,size=digest_stream(stream)
            if digest!=entry['sha256'] or size!=entry['size']:return False
        return True
    except (OSError,ValueError,KeyError,TypeError):return False


def installation_receipt(root):
    path=root/'state/installation.json'
    if path.is_symlink():raise ValueError('INSTALL_SYMLINK')
    if not path.exists():return None
    value=json.loads(read_private_bytes(path,4096),object_pairs_hook=unique_object)
    if (not isinstance(value,dict) or set(value)!={'schema','phase','releaseId','archiveHash','manifestHash','stage'}
            or type(value['schema']) is not int or value['schema']!=1
            or value['phase'] not in ('prepared','staged','stopped','replaced','committed')
            or not isinstance(value['releaseId'],str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',value['releaseId'])
            or not isinstance(value['stage'],str) or not re.fullmatch(r'\.stage-[a-f0-9]{32}',value['stage'])
            or any(not isinstance(value[k],str) or not re.fullmatch('[a-f0-9]{64}',value[k]) for k in ('archiveHash','manifestHash'))):
        raise ValueError('INSTALL_RECEIPT')
    return value


def prune_stale_stages(releases, keep=2):
    try:
        pattern=re.compile(r'^\.(failed|replaced|recovery-old)-[a-f0-9]{32}$')
        stale=[entry for entry in releases.iterdir() if pattern.match(entry.name) and entry.is_dir() and not entry.is_symlink()]
        if len(stale)>keep:
            stale.sort(key=lambda p:p.stat().st_mtime)
            for old in stale[:-keep]:
                shutil.rmtree(old,ignore_errors=True)
    except OSError:pass


def install_candidate(release, root, mode, failpoint=lambda phase:None,stop_active=None):
    if mode not in ('fresh','repair'):raise ValueError('INSTALL_MODE')
    root=Path(root).absolute();private_directory(root)
    with install_lock(root):
        for name in ('releases','config','secrets','state','logs','run','backups'):private_directory(root/name)
        anchor=root/'config/release-trust.pub'
        if anchor.is_symlink():raise ValueError('INSTALL_TRUST_KEY_SYMLINK')
        if anchor.exists():
            if read_private_bytes(anchor,32)!=release.trust_key:raise ValueError('INSTALL_TRUST_KEY_CHANGED')
        else:
            if len(release.trust_key)!=32:raise ValueError('INSTALL_TRUST_KEY')
            atomic_replace(anchor,release.trust_key)
        manifest=json.loads(release.signed_manifest,object_pairs_hook=unique_object)
        release_id=manifest['releaseId']
        if not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',release_id):raise ValueError('INSTALL_RELEASE_ID')
        active=active_release(root)
        if mode=='fresh' and active is not None:raise ValueError('INSTALL_ALREADY_ACTIVE_USE_UPDATE')
        target=root/'releases'/release_id
        if target.is_symlink():raise ValueError('INSTALL_SYMLINK')
        existing_manifest=target/'verified-manifest.json'
        if existing_manifest.is_symlink():raise ValueError('INSTALL_SYMLINK')
        if existing_manifest.exists():
            try:
                old_raw=read_private_bytes(existing_manifest,128*1024)
                old=json.loads(old_raw,object_pairs_hook=unique_object)
                verify_signed_manifest(old_raw,release.trust_key,old['architecture'],old['pythonAbi'],old['dependencies'])
            except (ValueError,KeyError,TypeError):pass  # Corrupt metadata can be repaired from the trusted archive.
            else:
                if old_raw!=release.signed_manifest:raise ValueError('INSTALL_RELEASE_ID_COLLISION')
        receipt_path=root/'state/installation.json'
        if receipt_path.is_symlink():raise ValueError('INSTALL_SYMLINK')
        previous=installation_receipt(root)
        if previous:
            if previous['phase']!='committed' and (previous.get('archiveHash')!=release.archive_hash or previous.get('releaseId')!=release_id):
                raise ValueError('INSTALL_RECOVERY_REQUIRED')
        if slot_verified(target,release.signed_manifest):
            if previous and previous['phase']!='committed':
                previous['phase']='committed';atomic_replace(receipt_path,json.dumps(previous).encode())
            return {'releaseId':release_id,'activated':False,'alreadyVerified':True}
        if target.exists() and (mode!='repair' or active==release_id and stop_active is None):
            raise ValueError('INSTALL_ACTIVE_REPAIR_REQUIRES_COORDINATED_STOP')
        releases=root/'releases'
        # A prior partial stage is code-only, retained for diagnosis under a bounded path.
        if previous and previous['phase']!='committed':
            name=previous.get('stage')
            if not isinstance(name,str) or not re.fullmatch(r'\.stage-[a-f0-9]{32}',name):raise ValueError('INSTALL_RECEIPT')
            old_stage=releases/name
            if old_stage.is_symlink():raise ValueError('INSTALL_SYMLINK')
            if old_stage.exists():
                old_stage.rename(releases/('.failed-'+uuid.uuid4().hex))
                prune_stale_stages(releases)
        required=sum(entry['size'] for entry in manifest['files'].values())+4*1024*1024
        if shutil.disk_usage(root).free<required:raise ValueError('INSTALL_DISK_SPACE')
        stage=releases/('.stage-'+uuid.uuid4().hex)
        receipt={'schema':1,'releaseId':release_id,'archiveHash':release.archive_hash,
                 'manifestHash':hashlib.sha256(release.signed_manifest).hexdigest(),'stage':stage.name,'phase':'prepared'}
        def record(phase):
            receipt['phase']=phase;atomic_replace(receipt_path,json.dumps(receipt,sort_keys=True).encode());failpoint(phase)
        record('prepared')
        extract_release(release,stage)
        if not slot_verified(stage,release.signed_manifest):raise ValueError('INSTALL_STAGE_HASH')
        sync_directory(stage);record('staged')
        if target.exists():
            if active==release_id:
                if not stop_active():raise RuntimeError('INSTALL_ACTIVE_STOP_FAILED')
                record('stopped')
            target.rename(releases/('.replaced-'+uuid.uuid4().hex));sync_directory(releases);record('replaced')
            prune_stale_stages(releases)
        stage.rename(target);sync_directory(releases);failpoint('promoted')
        record('committed')
        return {'releaseId':release_id,'activated':False,'alreadyVerified':False}


def recover_installation(root,key,runtime,stop):
    root=Path(root).absolute()
    with install_lock(root):
        value=installation_receipt(root)
        if value is None or value['phase']=='committed':return {'recovered':False,'pending':False}
        if read_private_bytes(root/'config/release-trust.pub',32)!=key:raise ValueError('INSTALL_TRUST_KEY_CHANGED')
        releases=root/'releases';target=releases/value['releaseId'];stage=releases/value['stage']
        if target.is_symlink() or stage.is_symlink():raise ValueError('INSTALL_SYMLINK')
        def matching_slot(slot):
            try:
                raw=read_private_bytes(slot/'verified-manifest.json',128*1024)
                manifest=verify_signed_manifest(raw,key,*runtime)
                return (manifest['releaseId']==value['releaseId']
                        and hashlib.sha256(raw).hexdigest()==value['manifestHash'] and slot_verified(slot,raw))
            except (OSError,ValueError,KeyError,TypeError):return False
        if not matching_slot(target):
            active=active_release(root)
            if not matching_slot(stage):
                return {'recovered':False,'pending':True,'activeUnaffected':active is not None and active!=value['releaseId']}
            if active==value['releaseId'] and not stop():raise RuntimeError('INSTALL_RECOVERY_STOP_FAILED')
            if target.exists():
                target.rename(releases/('.recovery-old-'+uuid.uuid4().hex))
                prune_stale_stages(releases)
            stage.rename(target);sync_directory(releases)
        value['phase']='committed';atomic_replace(root/'state/installation.json',json.dumps(value,sort_keys=True).encode())
    return {'recovered':True,'pending':False}
