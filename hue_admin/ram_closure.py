"""Fail-closed self audit before releasing a removable filesystem.

Only observes the calling process. Does not prove other processes are stopped,
prevent future lazy imports, or authorize any unmount. Run after required modules
are loaded and repeat before the actual maintenance transition.
"""
import os
from pathlib import Path


def mappings_for_device(text,major,minor):
    count=0
    for line in text.splitlines():
        fields=line.split()
        if len(fields)<5:raise ValueError('RAM_MAPS_FORMAT')
        device=fields[3].split(':')
        if len(device)!=2:raise ValueError('RAM_MAPS_FORMAT')
        if (int(device[0],16),int(device[1],16))==(major,minor):count+=1
    return count


def assert_independent(device,*,proc=Path('/proc/self'),allowed_device_fds=()):
    """Expected readonly block handles may be retained; USB files may not."""
    major,minor=os.major(device),os.minor(device)
    proc=Path(proc)
    maps=mappings_for_device((proc/'maps').read_text(),major,minor)
    cwd=(proc/'cwd').stat().st_dev==device
    executable=(proc/'exe').stat().st_dev==device
    files=[];blocks=[]
    for path in (proc/'fd').iterdir():
        try:info=path.stat()
        except FileNotFoundError:continue  # proc iterator FD closed after listing.
        if info.st_dev==device:files.append(int(path.name))
        import stat
        if stat.S_ISBLK(info.st_mode) and info.st_rdev==device:
            if int(path.name) not in allowed_device_fds:blocks.append(int(path.name))
    value={'usbMappings':maps,'usbFileDescriptors':len(files),'cwdOnUsb':cwd,
           'executableOnUsb':executable,'unexpectedBlockHandles':len(blocks)}
    if maps or files or blocks or cwd or executable:
        error=RuntimeError('RAM_RUNTIME_DEPENDENCY');error.diagnostic=value
        raise error
    return value
