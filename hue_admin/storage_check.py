"""Trusted offline checker: no mount/unmount, repair, formatter or public CLI.

Caller owns a reviewed maintenance window and a readonly pinned block FD.
This module obtains its own exclusive claim, invokes only verified e2fsck -f -n,
and returns clean evidence only after readback. Root/runtime provisioning is
trusted; this is not a defense against a malicious root or USB impersonation.
"""
import hashlib,json,math,os,re,secrets,select,signal,stat,subprocess,threading,time
from pathlib import Path

from .acceptance_journal import boot_id
from .acceptance_watchdog import _live_group,guard_parent
from .storage_acceptance import NAME,current_storage,identity_from_fd,validate_evidence

OUTPUT_LIMIT=4*1024*1024


def readonly_fd(fd):
    import fcntl
    return fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY


def mounted_targets(device,text=None):
    number=f'{os.major(device)}:{os.minor(device)}'
    text=Path('/proc/self/mountinfo').read_text() if text is None else text
    return [line.split()[4] for line in text.splitlines() if len(line.split())>5 and line.split()[2]==number]


class Runtime:
    """Lock supplied by trusted tool preparation, never by a network request."""
    def __init__(self,directory,lock):
        self.directory=Path(directory).absolute()
        if (not isinstance(lock,dict) or set(lock)!={'schema','toolVersion','files','links'}
                or type(lock['schema']) is not int or lock['schema']!=1 or lock['toolVersion']!='1.47.3'
                or not isinstance(lock['files'],dict) or not 2<=len(lock['files'])<=32
                or not {'e2fsck','ld.so.1'}<=lock['files'].keys()
                or not isinstance(lock['links'],dict) or len(lock['links'])>32):raise ValueError('FSCK_RUNTIME_LOCK')
        def name(value):return type(value) is str and (value in ('e2fsck','ld.so.1') or re.fullmatch(r'lib[A-Za-z0-9_+.-]+\.so(?:\.[0-9]+)*',value))
        if (any(not name(k) or type(v) is not str or not re.fullmatch('[a-f0-9]{64}',v) for k,v in lock['files'].items())
                or any(not name(k) or k in lock['files'] or k in ('e2fsck','ld.so.1')
                       or type(v) is not str or v not in lock['files'] for k,v in lock['links'].items())):
            raise ValueError('FSCK_RUNTIME_LOCK')
        self.lock=json.loads(json.dumps(lock))
        self.digest=hashlib.sha256(json.dumps(self.lock,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def verify_files(self):
        root=self.directory
        if (os.geteuid()!=0 or not root.is_relative_to(Path('/tmp')) or root.is_symlink()
                or any(p.is_symlink() for p in root.parents)):raise ValueError('FSCK_RUNTIME_LOCATION')
        info=root.stat()
        if not root.is_dir() or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('FSCK_RUNTIME_OWNER')
        if {p.name for p in root.iterdir()}!=set(self.lock['files'])|set(self.lock['links']):raise ValueError('FSCK_RUNTIME_CONTENTS')
        for name,expected in self.lock['files'].items():
            path=root/name;info=path.lstat()
            mode=0o700 if name in ('e2fsck','ld.so.1') else 0o600
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1
                    or stat.S_IMODE(info.st_mode)!=mode or info.st_size>16*1024*1024):raise ValueError('FSCK_RUNTIME_FILE')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('FSCK_RUNTIME_HASH')
        for name,target in self.lock['links'].items():
            path=root/name
            if not path.is_symlink() or path.lstat().st_uid!=0 or os.readlink(path)!=target:raise ValueError('FSCK_RUNTIME_LINK')

    def command(self,*arguments):
        root=self.directory
        return [str(root/'ld.so.1'),'--library-path',str(root),str(root/'e2fsck'),*arguments]

    def verify(self):
        self.verify_files()
        root=self.directory
        env={'PATH':'/usr/bin:/bin','LC_ALL':'C'}
        listing=subprocess.run([str(root/'ld.so.1'),'--library-path',str(root),'--list',str(root/'e2fsck')],
                               capture_output=True,text=True,timeout=15,env=env)
        if listing.returncode:raise ValueError('FSCK_RUNTIME_DEPENDENCIES')
        for line in listing.stdout.splitlines():
            if not line.strip():continue
            candidate=(line.split('=>',1)[1] if '=>' in line else line).strip().split()[0]
            if candidate.startswith(('linux-vdso','linux-gate')):continue
            path=Path(candidate)
            if (not path.is_absolute() or path.parent!=root
                    or path.name not in set(self.lock['files'])|set(self.lock['links'])):
                raise ValueError('FSCK_RUNTIME_EXTERNAL_DEPENDENCY')
        version=subprocess.run(self.command('-V'),capture_output=True,text=True,timeout=15,env=env)
        if version.returncode or not re.search(r'\be2fsck 1\.47\.3(?:\s|$)',version.stdout+version.stderr):
            raise ValueError('FSCK_RUNTIME_VERSION')

    def run(self,fd,timeout):
        """Low-level readonly tool run; regular-image runs never mint evidence."""
        from .owned_command import run_owned
        try:
            return run_owned(self.command('-f','-n',f'/proc/self/fd/{fd}'),pass_fds=(fd,),timeout=timeout,cwd=self.directory)
        except (ValueError,RuntimeError) as error:
            if error.args and type(error.args[0]) is str and error.args[0].startswith('COMMAND_'):
                error.args=('FSCK_'+error.args[0][len('COMMAND_'):],*error.args[1:])
            raise



def check_offline(fd,expected_identity,runtime,timeout=600):
    """Return evidence only for a clean real offline exclusive block check."""
    info=os.fstat(fd)
    if not stat.S_ISBLK(info.st_mode):raise ValueError('STORAGE_BLOCK_REQUIRED')
    if not readonly_fd(fd):raise ValueError('STORAGE_READONLY_REQUIRED')
    if mounted_targets(info.st_rdev):raise ValueError('STORAGE_STILL_MOUNTED')
    # Reopen the stable self descriptor as an exclusive block claim. readonly
    # e2fsck does not request O_EXCL itself, so this claim prevents mount races.
    claim=os.open(f'/proc/self/fd/{fd}',os.O_RDONLY|os.O_EXCL|os.O_NONBLOCK)
    try:
        if os.fstat(claim).st_rdev!=info.st_rdev or not readonly_fd(claim):raise ValueError('STORAGE_DEVICE_CHANGED')
        before=identity_from_fd(claim)
        if before!=expected_identity:raise ValueError('STORAGE_DEVICE_CHANGED')
        origin=boot_id()
        if mounted_targets(info.st_rdev):raise ValueError('STORAGE_STILL_MOUNTED')
        runtime.verify()
        result=runtime.run(claim,timeout)
        if type(result['exitCode']) is not int or result['exitCode']!=0:
            error=RuntimeError('STORAGE_CHECK_UNCLEAN');error.diagnostic=result;raise error
        runtime.verify_files()
        if (identity_from_fd(claim)!=before or mounted_targets(info.st_rdev)
                or boot_id()!=origin):raise ValueError('STORAGE_CHECK_CHANGED')
        value={'schema':1,'kind':'offline-e2fsck','targetKind':'block','readOnly':True,'exitCode':0,
               'toolVersion':'1.47.3','runtimeSha256':runtime.digest,'outputSha256':result['outputSha256'],
               'bootId':origin,'checkedMonotonic':time.monotonic(),'mountsBefore':[],'mountsAfter':[],
               'identity':before}
        validate_evidence(value,before,origin,time.monotonic())
        return value
    finally:os.close(claim)


def publish(root,value):
    """Trusted runner only, after remount. No CLI imports caller-authored JSON."""
    if os.name!='posix' or os.geteuid()!=0:raise ValueError('STORAGE_NATIVE_ROOT_REQUIRED')
    validate_evidence(value,current_storage(root),boot_id(),time.monotonic())
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW
    root_fd=os.open(root,flags);directory=None;created=False
    temporary='.storage-evidence-'+secrets.token_hex(16)
    try:
        info=os.fstat(root_fd)
        if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('STORAGE_ROOT_MODE')
        directory=os.open('state',flags,dir_fd=root_fd)
        info=os.fstat(directory)
        if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('STORAGE_STATE_MODE')
        try:old=os.stat(NAME,dir_fd=directory,follow_symlinks=False)
        except FileNotFoundError:pass
        else:
            if (not stat.S_ISREG(old.st_mode) or old.st_uid!=0 or old.st_nlink!=1
                    or stat.S_IMODE(old.st_mode)!=0o600 or old.st_size>8192):raise ValueError('STORAGE_EVIDENCE_FILE')
        data=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        if len(data)>8192:raise ValueError('STORAGE_EVIDENCE_FILE')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        created=True
        try:
            os.fchmod(fd,0o600);remaining=memoryview(data)
            while remaining:
                count=os.write(fd,remaining)
                if count<=0:raise OSError('STORAGE_EVIDENCE_WRITE')
                remaining=remaining[count:]
            os.fsync(fd)
        finally:os.close(fd)
        os.replace(temporary,NAME,src_dir_fd=directory,dst_dir_fd=directory);created=False
        os.fsync(directory)
        fd=os.open(NAME,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        try:
            if os.read(fd,8193)!=data:raise ValueError('STORAGE_EVIDENCE_READBACK')
        finally:os.close(fd)
        return {'storageEvidencePublished':True}
    finally:
        if created:os.unlink(temporary,dir_fd=directory)
        if directory is not None:os.close(directory)
        os.close(root_fd)
