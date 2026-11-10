"""Fixed agent runtime paths and bounded reads of untrusted agent-owned data.

These helpers do not provision or activate a runtime. Root must never follow
an agent-created symlink/hardlink to read or change another principal's files.
Ownership is checked on the opened descriptor before any data is consumed.
"""
import os
from pathlib import Path
import stat
from contextlib import contextmanager, ExitStack
import hashlib
import json
import shutil
import tempfile

from .privileges import runtime_paths

MAX_AGENT_FILE=16*1024*1024
MAX_RECOVERY_JOURNAL=32*1024*1024


def _recovery_stamp(info):
    return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]


def _recovery_parent(path):
    """Pin every directory without following links, including intermediate ones."""
    if not path.is_absolute() or '..' in path.parts:raise ValueError('AGENT_RECOVERY_PATH')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child=os.open(part,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0),dir_fd=fd)
            os.close(fd);fd=child
        return fd
    except BaseException:
        os.close(fd);raise


def _copy_recovery_file(fd,destination,limit):
    digest=hashlib.sha256();total=0
    out=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        os.lseek(fd,0,os.SEEK_SET)
        while True:
            data=os.read(fd,min(65536,limit+1-total))
            if not data:break
            total+=len(data)
            if total>limit:raise ValueError('AGENT_RECOVERY_SIZE')
            digest.update(data)
            view=memoryview(data)
            while view:
                count=os.write(out,view)
                if count<=0:raise OSError('AGENT_RECOVERY_OUTPUT')
                view=view[count:]
        os.fsync(out)
    finally:os.close(out)
    return digest.hexdigest()


@contextmanager
def preserve_agent_recovery(path,uid,gid,output_dir):
    """Preserve opaque bytes before the UID worker may recover a stopped DB.

    Caller owns lifecycle/owner locks and proves the agent is stopped. Archives
    intentionally survive errors. Root never opens SQLite or removes journals.
    """
    if os.name!='posix' or os.geteuid()!=0:raise ValueError('AGENT_RECOVERY_ROOT')
    path=Path(path);output_dir=Path(output_dir)
    with ExitStack() as stack:
        try:parent=_recovery_parent(path.parent)
        except OSError:raise ValueError('AGENT_RECOVERY_PATH') from None
        stack.callback(os.close,parent)
        info=os.fstat(parent)
        if (info.st_uid,info.st_gid,stat.S_IMODE(info.st_mode))!=(uid,gid,0o700):
            raise ValueError('AGENT_RECOVERY_PARENT')
        def absent(name):
            try:os.stat(name,dir_fd=parent,follow_symlinks=False)
            except FileNotFoundError:return True
            return False
        if not all(absent(path.name+s) for s in ('-wal','-shm')):
            raise ValueError('AGENT_RECOVERY_UNSUPPORTED_SIDECAR')
        if absent(path.name+'-journal'):
            yield None;return
        opened={}
        for label,name,limit in (('main',path.name,MAX_AGENT_FILE),('journal',path.name+'-journal',MAX_RECOVERY_JOURNAL)):
            try:fd=os.open(name,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0),dir_fd=parent)
            except OSError:raise ValueError('AGENT_RECOVERY_FILE') from None
            stack.callback(os.close,fd);info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_gid!=gid
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or not 0<=info.st_size<=limit):
                raise ValueError('AGENT_RECOVERY_FILE')
            opened[label]=(fd,name,limit,_recovery_stamp(info))
        journal=opened['journal']
        if journal[3][2]<=512 or os.pread(journal[0],8,0)!=bytes.fromhex('d9d505f920a163d7'):
            yield None;return  # SQLite decides cold-journal behavior on the read-only path.
        # Preserve the pair, stage the pair, then reserve SQLite backup/output.
        required=2*sum(item[3][2] for item in opened.values())+2*MAX_AGENT_FILE+65536
        if shutil.disk_usage(output_dir).free<required:raise ValueError('AGENT_RECOVERY_SPACE')
        archive=Path(tempfile.mkdtemp(prefix='sqlite-recovery-',dir=output_dir))
        expected={}
        for label,(fd,name,limit,before) in opened.items():
            saved=archive/('database.sqlite'+('-journal' if label=='journal' else ''))
            digest=_copy_recovery_file(fd,saved,limit)
            if (_recovery_stamp(os.fstat(fd))!=before or
                    _recovery_stamp(os.stat(name,dir_fd=parent,follow_symlinks=False))!=before):
                raise ValueError('AGENT_RECOVERY_CHANGED')
            expected[label]={'stamp':before,'sha256':digest}
        # Recheck the complete pair after both copies, not just each file alone.
        for fd,name,limit,before in opened.values():
            if _recovery_stamp(os.fstat(fd))!=before:raise ValueError('AGENT_RECOVERY_CHANGED')
        from .restore_transaction import atomic_replace,sync_directory
        atomic_replace(archive/'receipt.json',json.dumps({'schema':1,'phase':'preserved','files':expected}).encode())
        sync_directory(archive);sync_directory(output_dir)
        descriptors=[]
        for leaf in ('database.sqlite','database.sqlite-journal'):
            fd=os.open(archive/leaf,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            stack.callback(os.close,fd);descriptors.append(fd)
        expected['backup_fds']=descriptors
        yield expected
        # A source change while the worker recovers its private copy must not
        # be silently replaced by an older snapshot during canonical export.
        for fd,name,limit,before in opened.values():
            if (_recovery_stamp(os.fstat(fd))!=before or
                    _recovery_stamp(os.stat(name,dir_fd=parent,follow_symlinks=False))!=before):
                raise ValueError('AGENT_RECOVERY_CHANGED')


def agent_runtime_paths(root,release_id):
    base=runtime_paths(root,release_id)['base']/'agent'
    config=base/'config';state=base/'state';run=base/'run';logs=base/'logs'
    return {'base':base,'code':base/'code'/release_id,'config_dir':config,'state_dir':state,
            'run_dir':run,'log_dir':logs,'config':config/'agent.json',
            'device_config':config/'device.json','token_file':config/'api.token',
            'device_token':config/'device.token','keystore':state/'bond.json',
            'journal':state/'commands.sqlite','pid_file':run/'agent.pid',
            'lock_file':run/'adapter.lock','health_socket':run/'health.sock','log_file':logs/'agent.log'}


def read_agent_private(path,uid,gid,limit=65536):
    if (any(type(value) is not int or not 1<=value<=2147483647 for value in (uid,gid))
            or type(limit) is not int or not 1<=limit<=MAX_AGENT_FILE):
        raise ValueError('AGENT_STORAGE_IDENTITY_OR_LIMIT')
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('AGENT_STORAGE_PATH')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_gid!=gid
                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1
                or not 0<=info.st_size<=limit):
            raise ValueError('AGENT_STORAGE_FILE')
        data=bytearray()
        while len(data)<=limit:
            chunk=os.read(fd,min(65536,limit+1-len(data)))
            if not chunk:return bytes(data)
            data.extend(chunk)
        raise ValueError('AGENT_STORAGE_SIZE')
    finally:os.close(fd)
