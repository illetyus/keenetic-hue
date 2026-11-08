"""Internal trusted native launcher; no public CLI or arbitrary command strings.

Guardian survives its launching SSH process. Controller and recovery are direct
owned children with bounded lifetimes. Only run_controlled may mutate a release,
and its signed archive/storage preflight remains mandatory. Power loss is covered
by the separate startup journal; detached service descendants are not killed here.
"""
import math
import os
import select
import signal
import threading
import time
import uuid

from .acceptance_watchdog import guard_parent


def _timeout(value, maximum):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<value<=maximum:
        raise ValueError('ACCEPTANCE_LAUNCH_TIMEOUT')


def _child(callback, timeout):
    parent=os.getpid()
    pid=os.fork()
    if pid==0:
        try:
            if not guard_parent(parent):os._exit(1)
            os._exit(0 if callback() is True else 1)
        except BaseException:os._exit(1)
    deadline=time.monotonic()+timeout
    while True:
        result=os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
        if result is not None:
            os.waitpid(pid,0)
            return 0 if result.si_code==os.CLD_EXITED and result.si_status==0 else 1
        if time.monotonic()>=deadline:
            # This is our unreaped child, not a PID loaded from a stale receipt.
            os.kill(pid,signal.SIGKILL)
            stop_deadline=time.monotonic()+5
            while True:
                stopped=os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
                if stopped is not None:
                    os.waitpid(pid,0)
                    return 124
                if time.monotonic()>=stop_deadline:
                    raise RuntimeError('ACCEPTANCE_CHILD_STOP_UNCONFIRMED')
                time.sleep(.02)
        time.sleep(.02)


def run_guardian(run,recover,timeout,recovery_timeout,*,on_exit=None):
    """Trusted callbacks only. Failed acceptance remains failed after cleanup."""
    _timeout(timeout,1200);_timeout(recovery_timeout,300)
    outcome=_child(run,timeout)
    restoration=_child(recover,recovery_timeout)
    code=outcome if outcome else restoration
    if on_exit is not None:on_exit(code)
    return code


def _detach_io(keep):
    null=os.open('/dev/null',os.O_RDWR)
    try:
        for fd in (0,1,2):os.dup2(null,fd)
    finally:
        if null>2:os.close(null)
    for name in os.listdir('/proc/self/fd'):
        fd=int(name)
        if fd<=2 or fd in keep:continue
        try:os.close(fd)
        except OSError as error:
            # The directory iterator's own descriptor is already gone.
            if error.errno!=9:raise


def detach(run,recover,*,timeout=600,recovery_timeout=180,preserve_fds=(),on_exit=None):
    _timeout(timeout,1200);_timeout(recovery_timeout,300)
    if not callable(run) or not callable(recover):raise ValueError('ACCEPTANCE_CALLBACK')
    if on_exit is not None and not callable(on_exit):raise ValueError('ACCEPTANCE_CALLBACK')
    if type(preserve_fds) is not tuple or len(set(preserve_fds))!=len(preserve_fds):
        raise ValueError('ACCEPTANCE_HANDOFF_FDS')
    for fd in preserve_fds:
        if type(fd) is not int or fd<=2:raise ValueError('ACCEPTANCE_HANDOFF_FDS')
        os.fstat(fd)
    if (os.name!='posix' or not hasattr(os,'WNOWAIT') or os.geteuid()!=0
            or threading.active_count()!=1):raise RuntimeError('ACCEPTANCE_NATIVE_ROOT_SINGLE_THREAD')
    ready_read,ready_write=os.pipe();go_read,go_write=os.pipe()
    try:pid=os.fork()
    except BaseException:
        for fd in (ready_read,ready_write,go_read,go_write):os.close(fd)
        raise
    if pid==0:
        try:
            os.close(ready_read);os.close(go_write)
            os.setsid();os.chdir('/');os.umask(0o077)
            _detach_io({ready_write,go_read,*preserve_fds})
            os.write(ready_write,b'R');os.close(ready_write)
            if os.read(go_read,1)!=b'G':os._exit(1)
            os.close(go_read)
            os._exit(run_guardian(run,recover,timeout,recovery_timeout,on_exit=on_exit))
        except BaseException:os._exit(1)
    os.close(ready_write);os.close(go_read)
    try:
        if not select.select([ready_read],[],[],5)[0] or os.read(ready_read,1)!=b'R':
            raise RuntimeError('ACCEPTANCE_GUARDIAN_NOT_READY')
        os.write(go_write,b'G')
        return pid  # Started is not a completed or successful acceptance.
    except BaseException:
        try:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
        os.waitpid(pid,0)
        raise
    finally:
        os.close(ready_read);os.close(go_write)


def launch_candidate(adapter,journal,baseline,candidate,operation_id,*,timeout=300):
    """Compose the fixed controller/recovery; adapter and journal are trusted.

    No caller-supplied shell, executable, module path or root credential is read.
    Caller must inspect the durable journal for outcome; returned PID is not
    acceptance proof. An installed signed entry and real storage gate are still
    prerequisites for production activation of this internal API.
    """
    from .controlled_acceptance import run_controlled
    from .acceptance_recovery import recover_interrupted
    _timeout(timeout,900)
    if type(operation_id) is not str or str(uuid.UUID(operation_id))!=operation_id:
        raise ValueError('ACCEPTANCE_OPERATION_ID')
    class ScopedJournal:
        def load(self):
            state=journal.load()
            if state['operationId']!=operation_id:raise ValueError('ACCEPTANCE_OPERATION_CHANGED')
            return state
        def save(self,value):journal.save(value)
    def run():
        value=run_controlled(adapter,baseline,candidate,journal.save,operation_id,timeout)
        return (value.get('phase')=='closed' and value.get('restoreVerified') is True
                and value.get('outcome')=='completed')
    def recover():
        while True:  # Guardian gives this separate child a strict lifetime budget.
            try:
                value=recover_interrupted(adapter,ScopedJournal(),journal.quiescent)
                return value.get('phase')=='closed' and value.get('restoreVerified') is True
            except RuntimeError as error:
                if str(error) not in ('ACCEPTANCE_OWNER_ACTIVE','MANAGER_BUSY','MANAGEMENT_BUSY'):raise
                time.sleep(.2)
    return detach(run,recover,timeout=timeout+180,recovery_timeout=180)
