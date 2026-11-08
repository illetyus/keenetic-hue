"""Root-private same-boot RAM journal/lock for one maintenance owner directory."""
from contextlib import contextmanager
import json,os,secrets,stat
from pathlib import Path
from .acceptance_journal import boot_id
from .manifest import unique_object
from .storage_owner import validate_state


class RamJournal:
    def __init__(self,directory):self.directory=Path(directory).absolute()

    def _directory(self):
        path=self.directory
        if not path.is_relative_to(Path('/tmp')) or path==Path('/tmp') or any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError('STORAGE_RAM_JOURNAL_PATH')
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        info=os.fstat(fd)
        if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
            os.close(fd);raise ValueError('STORAGE_RAM_JOURNAL_OWNER')
        return fd

    @contextmanager
    def exclusive(self):
        import fcntl
        directory=self._directory()
        try:fd=os.open('maintenance.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=directory)
        finally:os.close(directory)
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                    or stat.S_IMODE(info.st_mode)!=0o600):raise ValueError('STORAGE_RAM_LOCK')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('STORAGE_MAINTENANCE_BUSY') from None
            yield
        finally:os.close(fd)

    def _load(self,directory):
        fd=os.open('maintenance.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>4096):raise ValueError('STORAGE_RAM_RECORD')
            data=os.read(fd,4097)
            if len(data)>4096:raise ValueError('STORAGE_RAM_RECORD')
            value=json.loads(data,object_pairs_hook=unique_object)
            if (not isinstance(value,dict) or set(value)!={'schema','bootId','state'}
                    or type(value['schema']) is not int or value['schema']!=1 or value['bootId']!=boot_id()):
                raise ValueError('STORAGE_RAM_RECORD_BOOT')
            return validate_state(value['state'])
        finally:os.close(fd)

    def load(self):
        directory=self._directory()
        try:return self._load(directory)
        finally:os.close(directory)

    def save(self,value):
        value=validate_state(value);directory=self._directory();created=False
        name='.maintenance-'+secrets.token_hex(16)
        try:
            try:self._load(directory)
            except FileNotFoundError:pass
            data=json.dumps({'schema':1,'bootId':boot_id(),'state':value},sort_keys=True,separators=(',',':')).encode()
            fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory);created=True
            try:
                os.fchmod(fd,0o600);remaining=memoryview(data)
                while remaining:
                    count=os.write(fd,remaining)
                    if count<=0:raise OSError('STORAGE_RAM_WRITE')
                    remaining=remaining[count:]
                os.fsync(fd)
            finally:os.close(fd)
            os.replace(name,'maintenance.json',src_dir_fd=directory,dst_dir_fd=directory);created=False
            os.fsync(directory)
            if self._load(directory)!=value:raise ValueError('STORAGE_RAM_READBACK')
        finally:
            if created:os.unlink(name,dir_fd=directory)
            os.close(directory)
