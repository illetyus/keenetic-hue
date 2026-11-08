"""Strict readonly preparation for a real Entware maintenance adapter.

No stop, signal, umount, mount or checker invocation occurs in this module.
The live transition must retain trusted ownership and reread these invariants.
"""
import copy
import os
from pathlib import Path
import re
import stat


def validate_mount_plan(value):
    if not isinstance(value,dict) or set(value)!={'device','node','storage','mounts'}:
        raise ValueError('STORAGE_MOUNT_PLAN')
    if (type(value['device']) is not str or not re.fullmatch(r'[0-9]{1,5}:[0-9]{1,5}',value['device'])
            or type(value['node']) is not str or not re.fullmatch(r'/dev/sd[a-z][1-9][0-9]?',value['node'])
            or type(value['storage']) is not str or not re.fullmatch(r'/tmp/mnt/[A-Za-z0-9_-]{1,128}',value['storage'])
            or type(value['mounts']) is not list or len(value['mounts'])!=2):
        raise ValueError('STORAGE_MOUNT_PLAN')
    targets=set()
    for row in value['mounts']:
        if (not isinstance(row,dict) or set(row)!={'target','device','root','type','readonly'}
                or row['device']!=value['device'] or row['root']!='/' or row['type']!='ext4'
                or row['readonly'] is not False or type(row['target']) is not str):
            raise ValueError('STORAGE_MOUNT_PLAN')
        targets.add(row['target'])
    if targets!={'/opt',value['storage']}:raise ValueError('STORAGE_MOUNT_PLAN')
    return copy.deepcopy(value)


def validate_listener(executable,command):
    expected=[b'/opt/sbin/dropbear',b'-s',b'-p',b'192.168.1.1:222',b'-P',b'/opt/var/run/dropbear.pid']
    if executable!='/opt/sbin/dropbear' or type(command) is not bytes or command!=b'\0'.join(expected)+b'\0':
        raise ValueError('STORAGE_SSH_LISTENER_IDENTITY')
    return True


def verify_target_resolution(plan,mountinfo,device_at):
    plan=validate_mount_plan(plan)
    for target in ('/opt',plan['storage']):
        rows=[line.split() for line in mountinfo.splitlines() if len(line.split())>5 and line.split()[4]==target]
        if len(rows)!=1:raise ValueError('STORAGE_STACKED_MOUNT')
        if rows[0][2]!=plan['device'] or device_at(target)!=plan['device']:
            raise ValueError('STORAGE_TARGET_CHANGED')
    return True


def mounted_plan(root=Path('/opt/var/lib/keenetic-hue-system')):
    device=Path(root).stat().st_dev;number=f'{os.major(device)}:{os.minor(device)}'
    rows=[]
    mountinfo=Path('/proc/self/mountinfo').read_text()
    for line in mountinfo.splitlines():
        before,after=line.split(' - ',1);fields=before.split();filesystem=after.split()
        if fields[2]!=number:continue
        rows.append({'target':fields[4],'device':number,'root':fields[3],
                     'type':filesystem[0],'readonly':'ro' in fields[5].split(',')})
    storage=[r['target'] for r in rows if r['target']!='/opt']
    node=(Path('/sys/dev/block')/number).resolve(strict=True).name
    plan=validate_mount_plan({'device':number,'node':'/dev/'+node,
                             'storage':storage[0] if len(storage)==1 else '', 'mounts':rows})
    def device_at(path):
        value=Path(path).stat().st_dev
        return f'{os.major(value)}:{os.minor(value)}'
    verify_target_resolution(plan,mountinfo,device_at)
    if Path('/proc/self/mountinfo').read_text()!=mountinfo:
        raise ValueError('STORAGE_MOUNT_INVENTORY_CHANGED')
    return plan


def listener_identity():
    path=Path('/opt/var/run/dropbear.pid')
    if path.is_symlink():raise ValueError('STORAGE_SSH_PIDFILE')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_size>16 or info.st_mode&0o022:
            raise ValueError('STORAGE_SSH_PIDFILE')
        raw=os.read(fd,17).strip()
    finally:os.close(fd)
    if not re.fullmatch(b'[0-9]{1,10}',raw) or int(raw)<=1:raise ValueError('STORAGE_SSH_PIDFILE')
    pid=int(raw);proc=Path('/proc')/str(pid)
    first=(proc/'stat').read_text().rsplit(') ',1)[1].split()[19]
    validate_listener(os.readlink(proc/'exe'),(proc/'cmdline').read_bytes())
    if (proc/'status').read_text().split('Uid:',1)[1].splitlines()[0].split()!=['0']*4:
        raise ValueError('STORAGE_SSH_OWNER')
    second=(proc/'stat').read_text().rsplit(') ',1)[1].split()[19]
    if first!=second:raise ValueError('STORAGE_SSH_OWNER_CHANGED')
    return {'pid':pid,'startTicks':int(first)}


def physical_swap_conflict(device):
    expected=(Path('/sys/dev/block')/f'{os.major(device)}:{os.minor(device)}').resolve(strict=True)
    for row in Path('/proc/swaps').read_text().splitlines()[1:]:
        fields=row.split();info=Path(fields[0]).stat()
        if fields[1]=='file':
            if info.st_dev==device:return True
        elif fields[1]=='partition':
            number=info.st_rdev
            other=(Path('/sys/dev/block')/f'{os.major(number)}:{os.minor(number)}').resolve(strict=True)
            if other==expected or other==expected.parent or other.parent==expected.parent:return True
        else:raise ValueError('STORAGE_SWAP_TYPE')
    return False


def readonly_preflight():
    if os.geteuid()!=0:raise ValueError('STORAGE_ROOT_REQUIRED')
    plan=mounted_plan();listener=listener_identity()
    device=os.makedev(*map(int,plan['device'].split(':')))
    if physical_swap_conflict(device):raise ValueError('STORAGE_SWAP_ON_TARGET')
    return {'mountPlan':plan,'sshListener':listener,'swapConflict':False,
            'servicesStopped':False,'deviceUnmounted':False}
