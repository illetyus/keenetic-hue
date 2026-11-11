"""Recoverable provisioning of the stable loader; application/state stay separate."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from .activation import verify_slot
from .installation import install_lock
from .restore_current import management_lock
from .restore_transaction import atomic_replace,sync_directory
from .restore import write_private
from .snapshot import read_private_bytes


def bootstrap_receipt(root):
    path=root/'state/bootstrap.json'
    if path.is_symlink():raise ValueError('BOOTSTRAP_RECEIPT')
    if not path.exists():return None
    value=json.loads(read_private_bytes(path,8192))
    required={'schema','phase','releaseId','manifestHash','stage','backup','cliPath','initPath'}
    if (not isinstance(value,dict) or set(value)!=required or type(value['schema']) is not int or value['schema']!=1
            or value['phase'] not in ('prepared','old_saved','manager_installed','cli_installed','committed')
            or not isinstance(value['stage'],str) or not re.fullmatch(r'\.bootstrap-stage-[a-f0-9]{32}',value['stage'])
            or not isinstance(value['backup'],str) or not re.fullmatch('backups/bootstrap-[a-f0-9]{32}',value['backup'])
            or not isinstance(value['manifestHash'],str) or not re.fullmatch('[a-f0-9]{64}',value['manifestHash'])
            or not isinstance(value['releaseId'],str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',value['releaseId'])):
        raise ValueError('BOOTSTRAP_RECEIPT')
    return value


def manager_matches(directory,files):
    if directory.is_symlink() or not directory.is_dir():return False
    actual=set()
    for path in directory.rglob('*'):
        if path.is_symlink():return False
        if path.is_file():actual.add(path.relative_to(directory).as_posix())
    if actual!=set(files):return False
    return all(read_private_bytes(directory/name,1024*1024)==data for name,data in files.items())


def bootstrap_sync_status(root,release_id=None):
    root=Path(root)
    try:
        receipt=bootstrap_receipt(root)
    except (OSError,ValueError,KeyError,TypeError):
        return {'bootstrapReleaseId':None,'bootstrapInSync':False}
    if receipt is None or receipt['phase']!='committed':
        return {'bootstrapReleaseId':receipt['releaseId'] if receipt else None,'bootstrapInSync':False}
    boot_id=receipt['releaseId']
    if release_id is None or boot_id!=release_id:
        return {'bootstrapReleaseId':boot_id,'bootstrapInSync':False}
    try:
        slot=root/'releases'/release_id
        manifest=json.loads(read_private_bytes(slot/'verified-manifest.json',128*1024))
        files={name[len('manager/'):]:read_private_bytes(slot/name,1024*1024)
               for name in manifest['files'] if name.startswith('manager/')}
        in_sync=bool(files) and manager_matches(root/'manager',files)
    except (OSError,ValueError,KeyError,TypeError):
        in_sync=False
    return {'bootstrapReleaseId':boot_id,'bootstrapInSync':in_sync}


def provision_bootstrap(root,release_id,key,runtime,cli_path,init_path,failpoint=lambda phase:None,replace_entries=False,locked=False):
    root=Path(root).absolute()
    if not locked:
        with management_lock(root):
            return provision_bootstrap(root,release_id,key,runtime,cli_path,init_path,failpoint,replace_entries,True)
    with install_lock(root):
        slot=verify_slot(root,release_id,key,runtime)
        signed=read_private_bytes(slot/'verified-manifest.json',128*1024);manifest=json.loads(signed)
        files={name[len('manager/'):]:read_private_bytes(slot/name,1024*1024) for name in manifest['files'] if name.startswith('manager/')}
        if not {'hue_admin/__init__.py','hue_admin/cli.py','huectl-entry.py'}.issubset(files):raise ValueError('BOOTSTRAP_MANAGER_FILES')
        if sum(map(len,files.values()))>4*1024*1024:raise ValueError('BOOTSTRAP_MANAGER_SIZE')
        cli=Path(cli_path).absolute();init=Path(init_path).absolute()
        desired={cli:read_private_bytes(slot/'service/huectl',65536),
                 init:read_private_bytes(slot/'service/S97keenetic-hue',65536)}
        hook=None
        if 'service/hue-usb-event' in manifest['files']:
            # Derived from the already fixed init destination, never event input.
            hook=init.parent.parent/'ndm/usb.d/50-keenetic-hue'
            desired[hook]=read_private_bytes(slot/'service/hue-usb-event',65536)
        receipt=bootstrap_receipt(root);digest=hashlib.sha256(signed).hexdigest()
        if receipt and receipt['phase']!='committed' and (receipt['releaseId']!=release_id or receipt['manifestHash']!=digest):
            raise ValueError('BOOTSTRAP_RESUME_SAME_CANDIDATE')
        if receipt and (receipt['cliPath']!=str(cli) or receipt['initPath']!=str(init)):
            raise ValueError('BOOTSTRAP_ENTRY_PATH_CHANGED')
        prior_entries={}
        if receipt:
            if receipt['phase']=='committed':
                prior_service=root/'releases'/receipt['releaseId']/'service'
                for path,sname in ((cli,'huectl'),(init,'S97keenetic-hue'),(hook,'hue-usb-event')):
                    if path is None:continue
                    cand=prior_service/sname
                    if cand.exists() and not cand.is_symlink() and cand.is_file():
                        try:prior_entries.setdefault(path,[]).append(read_private_bytes(cand,65536))
                        except (OSError,ValueError):pass
            else:
                saved_dir=root/receipt['backup']
                for index,path in enumerate(desired):
                    prev=saved_dir/f'entry-{index}.previous'
                    if prev.exists() and not prev.is_symlink() and prev.is_file():
                        try:prior_entries.setdefault(path,[]).append(read_private_bytes(prev,65536))
                        except (OSError,ValueError):pass
        for path,data in desired.items():
            if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('BOOTSTRAP_ENTRY_SYMLINK')
            if path.exists():
                existing=read_private_bytes(path,65536)
                if existing!=data and not replace_entries and existing not in prior_entries.get(path,()):
                    raise ValueError('BOOTSTRAP_UNRELATED_ENTRY')
        from .runtime_policy import release_policy
        if release_policy(root,release_id)['agent']=='dedicated':
            # A fresh restore validates SQLite under this UID before starting
            # an agent. Provision at trusted install, never as a dry-run effect.
            from .account_store import ensure_accounts
            ensure_accounts(root)
        matching=manager_matches(root/'manager',files)
        if receipt and receipt['phase']=='committed' and receipt['manifestHash']==digest and matching and all(p.exists() and read_private_bytes(p,65536)==data for p,data in desired.items()):
            return {'alreadyInstalled':True,'backup':receipt['backup']}
        if receipt is None or receipt['phase']=='committed':
            backup='backups/bootstrap-'+uuid.uuid4().hex;(root/backup).mkdir(mode=0o700)
            receipt={'schema':1,'phase':'prepared','releaseId':release_id,'manifestHash':digest,
                     'stage':'.bootstrap-stage-'+uuid.uuid4().hex,'backup':backup,
                     'cliPath':str(cli),'initPath':str(init)}
            for index,path in enumerate(desired):
                if path.exists():write_private(root/backup/f'entry-{index}.previous',read_private_bytes(path,65536))
        backup=root/receipt['backup']
        if backup.is_symlink() or not backup.is_dir():raise ValueError('BOOTSTRAP_BACKUP')
        def record(phase):
            receipt['phase']=phase;atomic_replace(root/'state/bootstrap.json',json.dumps(receipt,sort_keys=True).encode());failpoint(phase)
        record('prepared')
        if not matching:
            old_stage=root/receipt['stage']
            if old_stage.is_symlink():raise ValueError('BOOTSTRAP_STAGE_SYMLINK')
            if old_stage.exists():old_stage.rename(root/('.bootstrap-abandoned-'+uuid.uuid4().hex))
            receipt['stage']='.bootstrap-stage-'+uuid.uuid4().hex;record('prepared')
            stage=root/receipt['stage'];stage.mkdir(mode=0o700);candidate=stage/'manager';candidate.mkdir(mode=0o700)
            for name,data in files.items():
                path=candidate
                for part in name.split('/')[:-1]:path/=part;path.mkdir(mode=0o700,exist_ok=True)
                write_private(candidate/name,data);sync_directory((candidate/name).parent)
            if not manager_matches(candidate,files):raise ValueError('BOOTSTRAP_COPY_HASH')
            sync_directory(candidate)
            manager=root/'manager'
            if manager.is_symlink():raise ValueError('BOOTSTRAP_MANAGER_SYMLINK')
            if manager.exists():
                destination=backup/'manager'
                if destination.exists():destination=backup/('manager-displaced-'+uuid.uuid4().hex)
                manager.rename(destination);sync_directory(root);sync_directory(backup)
            record('old_saved')
            candidate.rename(manager);sync_directory(root)
            try:stage.rmdir();sync_directory(root)
            except OSError:pass
        record('manager_installed')
        atomic_replace(cli,desired[cli]);cli.chmod(0o700);record('cli_installed')
        atomic_replace(init,desired[init]);init.chmod(0o700)
        if hook is not None:
            for directory in (hook.parent.parent,hook.parent):
                if directory.is_symlink():raise ValueError('BOOTSTRAP_CALLBACK_DIRECTORY')
                if not directory.exists():directory.mkdir(mode=0o700);sync_directory(directory.parent)
                info=directory.stat()
                if not directory.is_dir() or os.name=='posix' and (info.st_uid!=0 or info.st_mode&0o022):
                    raise ValueError('BOOTSTRAP_CALLBACK_DIRECTORY')
            atomic_replace(hook,desired[hook]);hook.chmod(0o700)
        record('committed')
        for leftover in root.iterdir():
            if not leftover.is_symlink() and leftover.is_dir() and re.fullmatch(r'\.bootstrap-stage-[a-f0-9]{32}',leftover.name):
                try:leftover.rmdir()
                except OSError:pass
        return {'alreadyInstalled':False,'backup':receipt['backup'],'bootstrapInstalled':True}


def finish_if_needed(root,cli_path=Path('/opt/bin/huectl'),init_path=Path('/opt/etc/init.d/S97keenetic-hue')):
    value=bootstrap_receipt(root)
    if value is None or value['phase']=='committed':return
    if value['cliPath']!=str(Path(cli_path).absolute()) or value['initPath']!=str(Path(init_path).absolute()):raise ValueError('BOOTSTRAP_RECOVERY_ENTRY_PATH')
    from .release_commands import runtime_from_manifest
    key=read_private_bytes(root/'config/release-trust.pub',32)
    raw=read_private_bytes(root/'releases'/value['releaseId']/'verified-manifest.json',128*1024)
    runtime=runtime_from_manifest(raw,key)
    provision_bootstrap(root,value['releaseId'],key,runtime,cli_path,init_path,locked=True)
