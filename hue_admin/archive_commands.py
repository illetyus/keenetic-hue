"""Private archive file I/O; encrypted output is never overwritten."""
import os
import stat
import sys
from pathlib import Path

from .backup import seal, validate_new_backup_password
from .restore import open_backup
from .snapshot import capture_current, read_private_bytes
from .manifest import MAX_BYTES


def backup_to(path, password):
    validate_new_backup_password(password)
    path=Path(path).absolute()
    if path.exists() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('BACKUP_OUTPUT_EXISTS_OR_SYMLINK')
    info=path.parent.stat()
    if not stat.S_ISDIR(info.st_mode) or (sys.platform=='linux' and (info.st_mode & 0o077 or (hasattr(os,'geteuid') and os.geteuid()==0 and info.st_uid!=0))):
        raise ValueError('BACKUP_PRIVATE_DIRECTORY_REQUIRED')
    files,identity=capture_current()
    raw=seal(files,password,identity)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(raw);output.flush();os.fsync(output.fileno())
    except BaseException:
        path.unlink()
        raise
    return {'formatVersion':1,'files':len(files),'bytes':len(raw)}


def verify_backup(path, password, identity):
    return open_backup(read_private_bytes(path,MAX_BYTES),password,identity)
