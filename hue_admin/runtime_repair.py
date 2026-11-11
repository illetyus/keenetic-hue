"""Rebuild fixed runtime projections after stop; never reassign mutable files."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

from .manifest import unique_object
from .restore_transaction import atomic_replace,sync_directory


def receipt(root):
    from .service_manager import private_bytes,valid_id
    path=Path(root)/'state/runtime-repair.json'
    if not path.exists() and not path.is_symlink():return None
    value=json.loads(private_bytes(path,4096),object_pairs_hook=unique_object);info=path.lstat()
    if (info.st_uid!=0 or info.st_nlink!=1 or not isinstance(value,dict)
            or set(value)!={'schema','phase','releaseId','backup'}
            or type(value['schema']) is not int or value['schema']!=1
            or value['phase'] not in ('prepared','committed') or not valid_id(value['releaseId'])
            or not isinstance(value['backup'],str)
            or not re.fullmatch(r'backups/runtime-repair-[a-f0-9]{32}',value['backup'])):
        raise ValueError('RUNTIME_REPAIR_RECEIPT')
    return value


def _directory(path,uid,gid,mode,device,*,root_owned):
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('RUNTIME_REPAIR_SYMLINK')
    if not path.exists():
        if not root_owned:raise ValueError('RUNTIME_REPAIR_STATE_MISSING')
        path.mkdir(mode=0o700)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_dev!=device:
            raise ValueError('RUNTIME_REPAIR_DIRECTORY_OWNER')
        # The top directory already belongs to the intended principal. Only
        # its fixed group/mode change; no mutable child is read or chowned.
        os.fchmod(fd,0o700);os.fchown(fd,uid,gid);os.fchmod(fd,mode)
        after=path.lstat()
        if (after.st_dev,after.st_ino)!=(info.st_dev,info.st_ino):raise ValueError('RUNTIME_REPAIR_DIRECTORY_CHANGED')
    finally:os.close(fd)


def repair_runtime(root,release_id,*,resume=False,failpoint=lambda phase:None):
    from . import service_manager as manager
    from .agent_runtime import prepare_agent_runtime
    from .agent_storage import agent_runtime_paths
    from .installation import private_directory
    from .privileges import runtime_paths,load_service_identity,prepare_tunnel_runtime
    from .runtime_policy import release_policy,authenticated_manifest
    if os.name!='posix' or os.geteuid()!=0:raise ValueError('RUNTIME_REPAIR_ROOT_REQUIRED')
    root=Path(root).absolute();private_directory(root)
    if manager.selection(root)!=release_id:raise ValueError('RUNTIME_REPAIR_SELECTED_RELEASE_REQUIRED')
    manager.verify_selected(root,release_id)
    policy=release_policy(root,release_id)
    if policy['agent']!='dedicated' and policy['tunnel']!='dedicated':return {'runtimePrepared':False}
    for component in ('agent','tunnel'):
        record=manager.owner_record(component,root)
        if manager.supervisor_owned(component,root,record) or manager.child_pid(component,root) is not None:
            raise ValueError('RUNTIME_REPAIR_REQUIRES_STOP')
    private_directory(root/'backups');private_directory(root/'state')
    manifest=authenticated_manifest(root,release_id)
    size=sum(entry['size'] for name,entry in manifest['files'].items()
             if name=='bin/cloudflared' or name.startswith('agent/'))+4*1024*1024
    if shutil.disk_usage(root).free<size:raise ValueError('RUNTIME_REPAIR_DISK_SPACE')
    identity=None;agent=None;marker=None
    if policy['tunnel']=='dedicated':
        load_service_identity(root,'hue-tunnel')
    if policy['agent']=='dedicated':
        from .agent_runtime import _master,_marker
        from .usb_policy import pinned_transport
        identity=load_service_identity(root,'hue-agent')
        agent=agent_runtime_paths(root,release_id)
        marker=_marker(root,identity)
        cfg=json.loads(_master(root/'config/agent.json',8192),object_pairs_hook=unique_object)
        manager.validate_agent_config(root,cfg)
        usb=json.loads(_master(root/'config/usb.json',4096),object_pairs_hook=unique_object)
        if (not isinstance(usb,dict) or set(usb)!={'schema','vendor','product','serial'}
                or type(usb['schema']) is not int or usb['schema']!=1):
            raise ValueError('AGENT_USB_POLICY')
        pinned_transport({key:usb[key] for key in ('vendor','product','serial')})
    previous=receipt(root)
    if previous and previous['phase']=='prepared':
        if previous['releaseId']!=release_id:raise ValueError('RUNTIME_REPAIR_RECOVERY_REQUIRED')
        value=previous
    else:
        if resume:return {'runtimePrepared':False}
        value={'schema':1,'phase':'prepared','releaseId':release_id,'backup':'backups/runtime-repair-'+uuid.uuid4().hex}
    tunnel=runtime_paths(root,release_id);device=root.stat().st_dev
    if policy['agent']=='dedicated':
        if marker is not None and marker['phase']=='ready' and not agent['state_dir'].exists():
            raise ValueError('RUNTIME_REPAIR_STATE_MISSING')
    backup=root/value['backup'];private_directory(backup)
    atomic_replace(root/'state/runtime-repair.json',json.dumps(value,sort_keys=True).encode());failpoint('prepared')
    _directory(tunnel['base'],0,0,0o755,device,root_owned=True)
    targets=[]
    if policy['tunnel']=='dedicated':
        targets.extend((tunnel['base']/'bin',tunnel['credentials'],tunnel['data']))
    if policy['agent']=='dedicated':
        _directory(agent['base'],0,identity['gid'],0o710,device,root_owned=True)
        if agent['state_dir'].exists() or agent['state_dir'].is_symlink() or (marker is not None and marker['phase']=='ready'):
            _directory(agent['state_dir'],identity['uid'],identity['gid'],0o700,device,root_owned=False)
        targets.extend((agent['base']/'code',agent['config_dir'],agent['run_dir'],agent['log_dir']))
    for index,path in enumerate(targets):
        # Only these fixed names are moved. Symlinks are moved as links, never
        # followed, and all contents stay under the private root backup.
        if path.exists() or path.is_symlink():
            target=backup/('entry-'+str(index))
            if target.exists() or target.is_symlink():target=backup/('entry-'+str(index)+'-'+uuid.uuid4().hex)
            path.rename(target);sync_directory(path.parent);sync_directory(backup)
        failpoint('quarantined:'+str(index))
    if policy['tunnel']=='dedicated':prepare_tunnel_runtime(root,release_id)
    if policy['agent']=='dedicated':prepare_agent_runtime(root,release_id)
    value['phase']='committed'
    atomic_replace(root/'state/runtime-repair.json',json.dumps(value,sort_keys=True).encode());failpoint('committed')
    return {'runtimePrepared':True,'runtimeQuarantine':value['backup']}


def recover_if_needed(root):
    value=receipt(root)
    if value is None or value['phase']=='committed':return False
    repair_runtime(root,value['releaseId'],resume=True)
    return True
