"""Accept own private state or exact root-prepared read-only credentials."""
import os
from pathlib import Path
import stat


def private_file(path):
    path=Path(path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('PRIVATE_FILE_SYMLINK')
    info=path.lstat();uid=getattr(os,'geteuid',lambda:0)();gid=getattr(os,'getegid',lambda:0)()
    own=(info.st_uid==uid and info.st_gid==gid and stat.S_IMODE(info.st_mode) in (0o400,0o600))
    projected=(uid!=0 and gid!=0 and info.st_uid==0 and info.st_gid==gid and stat.S_IMODE(info.st_mode)==0o440)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not (own or projected):
        raise ValueError('PRIVATE_FILE_PERMISSIONS_REQUIRED')
    return path


def private_health_path(config):
    pid=config.get('pid_file');health=config.get('health_socket')
    if health is not None:
        if not isinstance(pid,str) or not isinstance(health,str) or Path(health)!=Path(pid).parent/'health.sock':
            raise ValueError('HEALTH_SOCKET_LAYOUT')
        return Path(health)
    if isinstance(pid,str) and Path(pid).parent==Path('/opt/var/lib/keenetic-hue-system/run'):
        return Path(pid).parent/'health.sock'
    return None
