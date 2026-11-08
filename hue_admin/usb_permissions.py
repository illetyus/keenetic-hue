"""Root grants one serial-pinned USB node; event-provided paths are never used."""
import json
import os
from pathlib import Path
import re
import stat

from .privileges import load_service_identity
from .restore_transaction import atomic_replace
from .runtime_policy import release_policy
from .service_manager import private_bytes,file_lock
from .usb_policy import pinned_transport,target_for_usb


def usb_inventory():
    devices=[]
    def text(path):
        with path.open('r',encoding='ascii') as source:value=source.read(129)
        if len(value)>128:raise ValueError('USB_ATTRIBUTE_SIZE')
        return value.strip()
    for path in Path('/sys/bus/usb/devices').iterdir():
        try:
            if not path.resolve().is_relative_to(Path('/sys/devices')):continue
            vendor=text(path/'idVendor');product=text(path/'idProduct')
            if (vendor,product)!=('050d','065a'):continue
            devices.append({'vendor':vendor,'product':product,'serial':text(path/'serial'),
                'bus':int(text(path/'busnum')),'device':int(text(path/'devnum')),'sysfsName':path.name})
        except (OSError,ValueError,UnicodeError):continue
    return devices


def ensure_usb_policy(root,*,dry_run=False,inventory=usb_inventory):
    if os.geteuid()!=0:raise ValueError('USB_ROOT_REQUIRED')
    root=Path(root);path=root/'config/usb.json'
    if path.exists() or path.is_symlink():
        value=json.loads(private_bytes(path,4096));info=path.lstat()
        if info.st_uid!=0 or info.st_nlink!=1:raise ValueError('USB_POLICY_OWNER')
        if (not isinstance(value,dict) or set(value)!={'schema','vendor','product','serial'}
                or type(value['schema']) is not int or value['schema']!=1):raise ValueError('USB_POLICY_SCHEMA')
        policy={key:value[key] for key in ('vendor','product','serial')};pinned_transport(policy)
        return policy
    candidates=inventory()
    # Initial pinning is allowed only for one unambiguous supported adapter.
    if len(candidates)!=1:raise ValueError('USB_INITIAL_IDENTITY_AMBIGUOUS')
    candidate=candidates[0];policy={key:candidate[key] for key in ('vendor','product','serial')}
    target_for_usb(candidates,policy)
    if not dry_run:atomic_replace(path,json.dumps({'schema':1,**policy},sort_keys=True).encode())
    return policy


def change_node_permissions(target,gid,guard,*,restore=False,check_only=False,ops=os):
    if type(gid) is not int or not 1<=gid<=2147483647:raise ValueError('USB_SERVICE_GROUP')
    if not isinstance(target,dict) or set(target)!={'path','major','minor','sysfsName'}:raise ValueError('USB_NODE_TARGET')
    match=re.fullmatch(r'/dev/bus/usb/([0-9]{3})/([0-9]{3})',target['path'])
    if not match:raise ValueError('USB_NODE_TARGET')
    bus,device=map(int,match.groups())
    if (not 1<=bus<=255 or not 1<=device<=127 or type(target['major']) is not int or target['major']!=189
            or type(target['minor']) is not int or target['minor']!=(bus-1)*128+device-1):raise ValueError('USB_NODE_TARGET')
    def valid(info):
        return (stat.S_ISCHR(info.st_mode) and info.st_uid==0 and info.st_nlink==1
                and (info.st_gid,stat.S_IMODE(info.st_mode)) in ((0,0o640),(gid,0o640),(gid,0o660))
                and (ops.major(info.st_rdev),ops.minor(info.st_rdev))==(target['major'],target['minor']))
    before=ops.lstat(target['path'])
    if not valid(before) or not guard():raise ValueError('USB_NODE_IDENTITY')
    original=(before.st_gid,stat.S_IMODE(before.st_mode));inode=(before.st_dev,before.st_ino)
    fd=ops.open(target['path'],os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        opened=ops.fstat(fd)
        if not valid(opened) or (opened.st_dev,opened.st_ino)!=inode:raise ValueError('USB_NODE_CHANGED')
        if check_only:
            if not guard():raise ValueError('USB_NODE_CHANGED')
            return
        def apply(group,mode):
            if mode==0o640:
                ops.fchmod(fd,mode);ops.fchown(fd,0,group)
            else:
                ops.fchown(fd,0,group);ops.fchmod(fd,mode)
        try:
            apply(0 if restore else gid,0o640 if restore else 0o660)
            current=ops.lstat(target['path'])
            if (current.st_dev,current.st_ino)!=inode or not guard():raise ValueError('USB_NODE_CHANGED')
        except BaseException:
            # Restore only this opened inode, never a newly substituted pathname.
            apply(*original)
            raise
    finally:ops.close(fd)


def prepare_usb_permissions(root,release_id,*,dry_run=False,running_guard=lambda:True):
    if os.geteuid()!=0:raise ValueError('USB_ROOT_REQUIRED')
    root=Path(root)
    if release_policy(root,release_id)['agent']!='dedicated':raise ValueError('USB_RESTRICTED_POLICY_REQUIRED')
    identity=load_service_identity(root,'hue-agent')
    def prepare():
        if not running_guard():return {'granted':False,'reason':'OWNER_NOT_RUNNING'}
        policy=ensure_usb_policy(root,dry_run=dry_run);target=target_for_usb(usb_inventory(),policy)
        def guard():
            return running_guard() and target_for_usb(usb_inventory(),policy)==target
        change_node_permissions(target,identity['gid'],guard,check_only=dry_run)
        return {'granted':not dry_run,'planned':dry_run,'node':target['path'],'gid':identity['gid']}
    if dry_run:return prepare()
    with file_lock(root/'run/usb-permissions.lock',wait=2):return prepare()


def refresh_usb_permissions(root):
    """Hotplug is only a trigger; all target data is read from fixed sysfs."""
    from . import service_manager as manager
    from .runtime_policy import release_policy as policy_for
    if os.geteuid()!=0:raise ValueError('USB_ROOT_REQUIRED')
    root=Path(root);release_id=manager.selection(root)
    if policy_for(root,release_id)['agent']!='dedicated':
        return {'granted':False,'reason':'LEGACY'}
    record=manager.owner_record('agent',root)
    def running():
        return bool(record and record['releaseId']==release_id and manager.selection(root)==release_id
                    and manager.supervisor_owned('agent',root,record))
    if not running():return {'granted':False,'reason':'OWNER_NOT_RUNNING'}
    try:return prepare_usb_permissions(root,release_id,running_guard=running)
    except ValueError as error:
        if str(error)=='USB_DEVICE_UNAVAILABLE':return {'granted':False,'reason':'DETACHED'}
        raise


def restore_usb_permissions(root,release_id):
    if os.geteuid()!=0:raise ValueError('USB_ROOT_REQUIRED')
    root=Path(root);identity=load_service_identity(root,'hue-agent')
    if release_policy(root,release_id)['agent']!='dedicated':return {'restored':False,'reason':'LEGACY'}
    if not (root/'config/usb.json').exists():return {'restored':False,'reason':'NOT_PREPARED'}
    with file_lock(root/'run/usb-permissions.lock',wait=2):
        policy=ensure_usb_policy(root)
        try:target=target_for_usb(usb_inventory(),policy)
        except ValueError as error:
            if str(error)=='USB_DEVICE_UNAVAILABLE':return {'restored':True,'reason':'DETACHED'}
            raise
        change_node_permissions(target,identity['gid'],lambda:target_for_usb(usb_inventory(),policy)==target,restore=True)
        return {'restored':True,'node':target['path']}
