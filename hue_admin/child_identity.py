"""Root-owned process generation records; a reused PID never grants ownership."""
import json
import os
from pathlib import Path
import stat

from .manifest import unique_object
from .restore_transaction import atomic_replace


def _path(root,component):
    if component not in ('agent','tunnel'):raise ValueError('CHILD_COMPONENT')
    return Path(root)/'run'/f'{component}-child.json'


def write_child_identity(root,component,release_id):
    from .service_manager import process,boot_id,valid_id
    if os.name=='posix' and os.geteuid()!=0:raise ValueError('CHILD_ROOT_WRITER_REQUIRED')
    if not valid_id(release_id):raise ValueError('CHILD_RELEASE_ID')
    pid=os.getpid();observed=process(pid)
    if observed is None:raise ValueError('CHILD_PROCESS_MISSING')
    value={'schema':1,'pid':pid,'releaseId':release_id,'bootId':boot_id(),'startTicks':observed[0]}
    atomic_replace(_path(root,component),json.dumps(value,sort_keys=True).encode())


def child_identity_matches(root,component,release_id,pid,start_ticks,boot):
    from .service_manager import private_bytes
    try:
        path=_path(root,component);raw=private_bytes(path,4096);info=path.lstat()
        if (info.st_nlink!=1 or os.name=='posix' and
                (info.st_uid!=0 or info.st_gid!=0 or stat.S_IMODE(info.st_mode)!=0o600)):
            return False
        value=json.loads(raw,object_pairs_hook=unique_object)
        return (isinstance(value,dict) and set(value)=={'schema','pid','releaseId','bootId','startTicks'}
                and type(value['schema']) is int and value['schema']==1
                and type(value['pid']) is int and value['pid']==pid
                and value['releaseId']==release_id and value['bootId']==boot
                and isinstance(value['startTicks'],str) and value['startTicks']==start_ticks)
    except (OSError,ValueError,KeyError,TypeError):return False
