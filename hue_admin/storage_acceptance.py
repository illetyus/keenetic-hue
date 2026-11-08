"""Read-only consumer of trusted offline-check evidence, not a check producer.

The privileged offline runner must create evidence from a real pinned block FD
and verified e2fsck runtime. No user flag/import command can mint this record.
Root-private evidence is trusted against service users, not a hostile root.
Identity is a freshness/association check, not cryptographic USB authentication.
"""
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .acceptance_journal import boot_id
from .manifest import unique_object

NAME='storage-acceptance.json'
MAX_AGE=900


def _identity(value):
    keys={'filesystemUuid','usbVendor','usbProduct','usbSerial','capacityBytes'}
    if not isinstance(value,dict) or set(value)!=keys:
        raise ValueError('STORAGE_IDENTITY')
    try:
        filesystem=uuid.UUID(value['filesystemUuid'])
        if str(filesystem)!=value['filesystemUuid'] or filesystem.int==0:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ValueError('STORAGE_UUID') from None
    if (any(type(value[key]) is not str or not re.fullmatch('[a-f0-9]{4}',value[key])
            for key in ('usbVendor','usbProduct'))
            or type(value['usbSerial']) is not str or not re.fullmatch('[A-Za-z0-9._:-]{1,128}',value['usbSerial'])
            or type(value['capacityBytes']) is not int or not 1024*1024<=value['capacityBytes']<2**63
            or value['capacityBytes']%512):
        raise ValueError('STORAGE_IDENTITY')
    return dict(value)


def validate_evidence(value,current,current_boot,now):
    keys={'schema','kind','targetKind','readOnly','exitCode','toolVersion','runtimeSha256',
          'outputSha256','bootId','checkedMonotonic','mountsBefore','mountsAfter','identity'}
    if (not isinstance(value,dict) or set(value)!=keys
            or type(value['schema']) is not int or value['schema']!=1
            or value['kind']!='offline-e2fsck' or value['targetKind']!='block'
            or value['readOnly'] is not True or type(value['exitCode']) is not int or value['exitCode']!=0
            or value['toolVersion']!='1.47.3' or value['bootId']!=current_boot
            or type(value['mountsBefore']) is not list or value['mountsBefore']
            or type(value['mountsAfter']) is not list or value['mountsAfter']
            or any(type(value[k]) is not str or not re.fullmatch('[a-f0-9]{64}',value[k])
                   for k in ('runtimeSha256','outputSha256'))):
        raise ValueError('STORAGE_OFFLINE_EVIDENCE')
    checked=value['checkedMonotonic']
    if (type(checked) not in (int,float) or not math.isfinite(checked)
            or type(now) not in (int,float) or not math.isfinite(now)
            or checked<0 or not 0<=now-checked<=MAX_AGE):
        raise ValueError('STORAGE_EVIDENCE_EXPIRED')
    if _identity(value['identity'])!=_identity(current):raise ValueError('STORAGE_DEVICE_CHANGED')
    return True


def identity_from_fd(fd):
    """Bounded ext4 UUID read from an already-open readonly real block device."""
    import fcntl
    info=os.fstat(fd)
    if not stat.S_ISBLK(info.st_mode):raise ValueError('STORAGE_BLOCK_REQUIRED')
    if fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE!=os.O_RDONLY:
        raise ValueError('STORAGE_READONLY_REQUIRED')
    device=Path('/sys/dev/block')/f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'
    device=device.resolve(strict=True)
    sectors=int((device/'size').read_text(encoding='ascii').strip())
    usb=None
    for ancestor in (device,*device.parents):
        if (ancestor/'idVendor').is_file() and (ancestor/'idProduct').is_file():
            usb=ancestor;break
    if usb is None:raise ValueError('STORAGE_USB_REQUIRED')
    superblock=os.pread(fd,1024,1024)
    if len(superblock)!=1024 or superblock[0x38:0x3a]!=b'\x53\xef':
        raise ValueError('STORAGE_EXT4_SIGNATURE')
    identity={'filesystemUuid':str(uuid.UUID(bytes=superblock[0x68:0x78])),
              'usbVendor':(usb/'idVendor').read_text(encoding='ascii').strip().lower(),
              'usbProduct':(usb/'idProduct').read_text(encoding='ascii').strip().lower(),
              'usbSerial':(usb/'serial').read_text(encoding='ascii').strip(),
              'capacityBytes':sectors*512}
    return _identity(identity)


def current_storage(root):
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    directory=os.open(root,flags)
    try:
        device=os.fstat(directory).st_dev
        number=f'{os.major(device)}:{os.minor(device)}'
        mounts=Path('/proc/self/mountinfo').read_text().splitlines()
        if not any(len(line.split())>6 and line.split()[2]==number
                   and line.split(' - ',1)[-1].split()[0]=='ext4' for line in mounts):
            raise ValueError('STORAGE_EXT4_MOUNT_REQUIRED')
        sysfs=(Path('/sys/dev/block')/number).resolve(strict=True)
        if not re.fullmatch('[A-Za-z0-9_-]{1,64}',sysfs.name):raise ValueError('STORAGE_DEVICE_NAME')
        fd=os.open('/dev/'+sysfs.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            if os.fstat(fd).st_rdev!=device:raise ValueError('STORAGE_DEVICE_CHANGED')
            return identity_from_fd(fd)
        finally:os.close(fd)
    finally:os.close(directory)


def _private_evidence(root):
    if os.geteuid()!=0:raise ValueError('STORAGE_ROOT_REQUIRED')
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    root_fd=os.open(root,flags)
    directory=None
    try:
        info=os.fstat(root_fd)
        if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('STORAGE_ROOT_MODE')
        directory=os.open('state',flags,dir_fd=root_fd)
        info=os.fstat(directory)
        if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('STORAGE_STATE_MODE')
        fd=os.open(NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>8192):
                raise ValueError('STORAGE_EVIDENCE_FILE')
            raw=os.read(fd,8193)
            if len(raw)>8192:raise ValueError('STORAGE_EVIDENCE_FILE')
            return json.loads(raw,object_pairs_hook=unique_object)
        finally:os.close(fd)
    finally:
        if directory is not None:os.close(directory)
        os.close(root_fd)


def require_current(root):
    value=_private_evidence(root)
    return validate_evidence(value,current_storage(root),boot_id(),time.monotonic())
