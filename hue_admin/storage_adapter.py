"""Real fixed maintenance IO; no CLI or automatic invocation.

Caller must supply the reviewed private RAM command runner, pinned readonly FD,
RAM journal/recovery ownership and exclusive maintenance window. This adapter
must never be called from the Entware-dependent SSH interpreter itself.
"""
import json
import os
from pathlib import Path
import subprocess
import time
from .owned_command import run_owned

from .storage_live import mounted_plan,listener_identity,physical_swap_conflict,validate_mount_plan
from .storage_acceptance import identity_from_fd
from .storage_check import check_offline,publish
from .ram_closure import assert_independent
from .acceptance_journal import process_token

ROOT=Path('/opt/var/lib/keenetic-hue-system')


class LiveIO:
    def __init__(self,plan,listener,device_fd,identity,runtime,ram_command):
        self.plan=validate_mount_plan(plan);self.listener=dict(listener)
        self.fd=device_fd;self.identity=dict(identity);self.runtime=runtime
        self.ram_command=ram_command  # trusted fixed loader/BusyBox runner, not shell text
        self.device=None
        self.guardian=None

    def prepare_process(self):
        # Called independently in both direct guardian children. Never depend on
        # Python object mutations made in a previous forked worker.
        import stat
        from .storage_check import readonly_fd
        info=os.fstat(self.fd)
        if not stat.S_ISBLK(info.st_mode) or not readonly_fd(self.fd) or not self.same_device():
            raise ValueError('STORAGE_PINNED_HANDLE')
        self.device=info.st_rdev
        self.guardian=process_token(os.getppid())
        if self.guardian is None:raise ValueError('STORAGE_GUARDIAN_ABSENT')

    def validate(self):
        if os.geteuid()!=0:raise ValueError('STORAGE_ROOT_REQUIRED')
        self.device=os.fstat(self.fd).st_rdev
        if mounted_plan()!=self.plan or not self.same_device():raise ValueError('STORAGE_DEVICE_CHANGED')
        if listener_identity()!=self.listener:raise ValueError('STORAGE_SSH_OWNER_CHANGED')
        if physical_swap_conflict(self.device):raise ValueError('STORAGE_SWAP_ON_TARGET')
        self.runtime.verify()

    def independent(self):
        assert_independent(self.device,allowed_device_fds=(self.fd,))
        return True

    def same_device(self):
        if identity_from_fd(self.fd)!=self.identity:return False
        current=os.open(self.plan['node'],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            return (os.fstat(current).st_rdev==os.fstat(self.fd).st_rdev
                    and identity_from_fd(current)==self.identity)
        finally:os.close(current)

    def target(self,name):
        if name=='opt':return '/opt'
        if name=='storage':return self.plan['storage']
        raise ValueError('STORAGE_MOUNT_NAME')

    def _mount_row(self,name):
        target=self.target(name)
        rows=[]
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields=line.split()
            if len(fields)>5 and fields[4]==target:rows.append(fields)
        if not rows:return None
        if len(rows)!=1 or rows[0][2]!=self.plan['device'] or rows[0][3]!='/':
            raise ValueError('STORAGE_MOUNT_CHANGED')
        number=Path(target).stat().st_dev
        if f'{os.major(number)}:{os.minor(number)}'!=self.plan['device']:
            raise ValueError('STORAGE_TARGET_CHANGED')
        return rows[0]

    def mounted(self,name):return self._mount_row(name) is not None

    def readonly(self,name):
        row=self._mount_row(name)
        if row is None:raise ValueError('STORAGE_MOUNT_ABSENT')
        return 'ro' in row[5].split(',')

    def _service(self,name,action):
        commands={('hue','stop'):['/opt/bin/huectl','internal-service','stop'],
                  ('hue','start'):['/opt/bin/huectl','internal-service','start'],
                  ('ssh','stop'):['/opt/etc/init.d/S51dropbear','stop'],
                  ('ssh','start'):['/opt/etc/init.d/S51dropbear','start']}
        result=run_owned(commands[name,action],timeout=90,cwd='/',
                         env={'PATH':'/opt/bin:/opt/sbin:/usr/bin:/bin','LC_ALL':'C'})
        return result['exitCode']==0

    def stop(self):
        if listener_identity()!=self.listener:raise ValueError('STORAGE_SSH_OWNER_CHANGED')
        if not self._service('hue','stop'):return False
        if listener_identity()!=self.listener:raise ValueError('STORAGE_SSH_OWNER_CHANGED')
        return self._service('ssh','stop')

    def quiescent(self):
        # Never terminate arbitrary holders. An open user SSH session aborts
        # maintenance and triggers the transaction's baseline service recovery.
        for item in Path('/proc').iterdir():
            if not item.name.isdigit():continue
            try:
                fields=(item/'stat').read_text().rsplit(') ',1)[1].split()
                if fields[0]=='Z':continue
                allowed=(self.fd,) if int(item.name)==os.getpid() else ()
                if self.guardian is not None and int(item.name)==self.guardian['pid']:
                    if process_token(int(item.name))!=self.guardian:return False
                    import stat
                    from .storage_check import readonly_fd
                    flags=dict(line.split(':',1) for line in (item/'fdinfo'/str(self.fd)).read_text().splitlines())
                    if int(flags['flags'].strip(),8)&os.O_ACCMODE!=os.O_RDONLY:return False
                    handle=os.open(item/'fd'/str(self.fd),os.O_RDONLY|os.O_NONBLOCK)
                    try:
                        info=os.fstat(handle)
                        if not stat.S_ISBLK(info.st_mode) or info.st_rdev!=self.device or not readonly_fd(handle):return False
                    finally:os.close(handle)
                    allowed=(self.fd,)
                assert_independent(self.device,proc=item,allowed_device_fds=allowed)
            except (FileNotFoundError,ProcessLookupError):continue
            except (RuntimeError,OSError,ValueError):return False
        return True

    def _ram(self,*args):
        # Runner provisioning/closure and per-command bounded process ownership
        # are caller requirements, identical for umount and recovery mount.
        result=self.ram_command(tuple(args))
        if result is not True:raise RuntimeError('STORAGE_RAM_COMMAND_FAILED')

    def recovery_ready(self):
        if not self.same_device():return False
        present=[self.mounted(name) for name in ('storage','opt')]
        if all(present):return True  # No remount is needed; normal owners may run.
        if not self.quiescent():return False
        if not any(present):
            # A surviving checker retains the exclusive claim. Never race its
            # reads with remount; no PID from a receipt is killed here.
            import errno
            try:claim=os.open(f'/proc/self/fd/{self.fd}',os.O_RDONLY|os.O_EXCL|os.O_NONBLOCK)
            except OSError as error:
                if error.errno==errno.EBUSY:return False
                raise
            try:
                if identity_from_fd(claim)!=self.identity:return False
            finally:os.close(claim)
        return True

    def unmount(self,name):
        target=self.target(name)
        if not self.same_device() or not self.mounted(name):raise ValueError('STORAGE_DEVICE_CHANGED')
        self._ram('umount',target)

    def mount(self,name,readonly):
        if type(readonly) is not bool or not self.same_device():raise ValueError('STORAGE_DEVICE_CHANGED')
        target=self.target(name)
        if name=='storage':
            options='ro,noload,noatime' if readonly else 'rw,noatime,data=ordered'
            self._ram('mount','-t','ext4','-o',options,self.plan['node'],target)
        else:
            self._ram('mount','-o','bind',self.plan['storage'],target)
            if readonly:self._ram('mount','-o','remount,bind,ro',target)

    def check(self):
        if not self.same_device():raise ValueError('STORAGE_DEVICE_CHANGED')
        return check_offline(self.fd,self.identity,self.runtime)

    def publish(self,value):return publish(ROOT,value).get('storageEvidencePublished') is True

    def start(self):
        return self._service('ssh','start') and self._service('hue','start')

    def healthy(self):
        command=['/opt/bin/huectl','health','--live','--json']
        environment={'PATH':'/opt/bin:/opt/sbin:/usr/bin:/bin','LC_ALL':'C'}
        deadline=time.monotonic()+60
        while True:
            try:
                listener_identity()
                remaining=deadline-time.monotonic()
                if remaining<=0:return False
                result=run_owned(command,timeout=min(15,remaining),cwd='/',env=environment)
                if (type(result) is dict and type(result.get('exitCode')) is int and result['exitCode']==0
                        and type(result.get('outputBytes')) is int and 0<result['outputBytes']<=8192
                        and type(result.get('outputTail')) is str):
                    value=json.loads(result['outputTail'])
                    components=value.get('components') if isinstance(value,dict) else None
                    expected={'agent':'running','tunnel':'ready','manager':'running',
                              'localApi':'ready','ble':'ready'}
                    if (isinstance(value,dict) and type(value.get('schemaVersion')) is int and value['schemaVersion']==1
                            and value.get('healthy') is True and isinstance(value.get('releaseId'),str)
                            and isinstance(components,dict)
                            and all(isinstance(components.get(name),dict)
                                    and components[name].get('state')==state
                                    for name,state in expected.items())):
                        return True
            except RuntimeError as error:
                if str(error) not in ('COMMAND_TIMEOUT','COMMAND_OUTPUT_LIMIT'):raise
            except (OSError,ValueError,TypeError,KeyError):pass
            remaining=deadline-time.monotonic()
            if remaining<=0:return False
            time.sleep(min(1,remaining))
