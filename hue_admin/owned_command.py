"""Bounded trusted fixed-command execution with owned group cleanup.

No shell evaluation. Caller controls trusted command/environment; untrusted
request data must never select these arguments. Detached service supervisors
retain their normal independent owners; this drains only this command group.
"""
import hashlib,math,os,select,signal,subprocess,threading,time
from .acceptance_watchdog import _live_group,guard_parent
OUTPUT_LIMIT=4*1024*1024


def run_owned(command,*,timeout,cwd,pass_fds=(),env=None):
    if not isinstance(command,(list,tuple)) or not command or any(type(v) is not str for v in command):
        raise ValueError('COMMAND_ARGUMENTS')
    env={'PATH':'/usr/bin:/bin','LC_ALL':'C'} if env is None else env
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=900:
        raise ValueError('COMMAND_TIMEOUT')
    if threading.active_count()!=1:raise ValueError('COMMAND_SINGLE_THREAD_REQUIRED')
    parent=os.getpid()
    digest=hashlib.sha256();count=0;tail=b'';eof=False;reason=None
    deadline=time.monotonic()+timeout;result=None;process=None
    cancellations={signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGQUIT}
    original_mask=signal.pthread_sigmask(signal.SIG_BLOCK,cancellations)
    def guarded():
        if not guard_parent(parent):os._exit(70)
        signal.pthread_sigmask(signal.SIG_SETMASK,original_mask)
    try:
        # Defer cancellation until the new child handle is assigned and its
        # cleanup owner is active. The child restores its own original mask.
        process=subprocess.Popen(command,stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,pass_fds=pass_fds,env=env,
            start_new_session=True,preexec_fn=guarded,cwd=cwd)
        signal.pthread_sigmask(signal.SIG_SETMASK,original_mask)
        while True:
            if select.select([process.stdout],[],[],.05)[0]:
                data=os.read(process.stdout.fileno(),65536)
                if not data:eof=True
                else:
                    count+=len(data);digest.update(data);tail=(tail+data)[-8192:]
                    if count>OUTPUT_LIMIT:reason='COMMAND_OUTPUT_LIMIT';break
            result=os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
            if result is not None:break
            if time.monotonic()>=deadline:reason='COMMAND_TIMEOUT';break
    finally:
        # Repeated cancellation must not interrupt stop confirmation. Keep
        # the leader unreaped and prefer an explicit unknown-stop outcome
        # over a pending cancellation when cleanup cannot prove termination.
        signal.pthread_sigmask(signal.SIG_BLOCK,cancellations)
        stop_error=None
        try:
            if process is not None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                stop_deadline=time.monotonic()+5
                while True:
                    stopped=os.waitid(os.P_PID,process.pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
                    if stopped is not None and not _live_group(process.pid):break
                    if time.monotonic()>=stop_deadline:raise RuntimeError('COMMAND_STOP_UNCONFIRMED')
                    time.sleep(.02)
                process.wait(timeout=1);process.stdout.close()
        except BaseException:
            stop_error=RuntimeError('COMMAND_STOP_UNCONFIRMED')
            # Retain the actual unreaped owned child, not only a recyclable PID.
            stop_error.ownedProcess=process
        finally:
            try:signal.pthread_sigmask(signal.SIG_SETMASK,original_mask)
            except BaseException:
                if stop_error is not None:raise stop_error
                raise
        if stop_error is not None:raise stop_error
    if reason:raise RuntimeError(reason)
    return {'exitCode':process.returncode,'outputSha256':digest.hexdigest(),
            'outputBytes':count,'outputTail':tail.decode(errors='replace')}