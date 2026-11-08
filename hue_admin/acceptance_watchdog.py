"""POSIX acceptance worker containment; no production command integration.

record must durably save the supplied snapshot before returning. recover must
verify the baseline and return True, not merely request restoration. Worker
children must remain in their dedicated process group. Supervisor death,
escaped groups and power loss require separate durable startup recovery.
"""
import copy,ctypes,math,os,select,signal,time,uuid
from pathlib import Path


def guard_parent(expected_parent,prctl=None,parent=os.getppid):
 """Child only: install Linux parent-death SIGKILL, then close the fork race.

 Not inherited by further forks and cleared by identity changes. Every owned
 child must install its own guard before running trusted mutation callbacks.
 """
 if type(expected_parent) is not int or expected_parent<=1:return False
 try:
  if prctl is None:
   libc=ctypes.CDLL(None,use_errno=True);prctl=libc.prctl
   prctl.argtypes=[ctypes.c_int,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong]
   prctl.restype=ctypes.c_int
  return prctl(1,9,0,0,0)==0 and parent()==expected_parent  # Linux SIGKILL=9.
 except (OSError,ValueError,AttributeError):return False


def _live_group(pgid):
 live=[]
 for path in Path('/proc').iterdir():
  if not path.name.isdigit():continue
  try:
   fields=(path/'stat').read_text().rsplit(') ',1)[1].split()
   if int(fields[2])==pgid and fields[0]!='Z':live.append(int(path.name))
  except (FileNotFoundError,ProcessLookupError):continue
 return live


def supervise(worker,recover,record,operation_id,timeout):
 if (type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=900
     or type(operation_id) is not str):raise ValueError('ACCEPTANCE_INPUT')
 try:
  if str(uuid.UUID(operation_id))!=operation_id:raise ValueError('ACCEPTANCE_INPUT')
 except (ValueError,AttributeError):raise ValueError('ACCEPTANCE_INPUT') from None
 if not hasattr(os,'fork') or not hasattr(os,'WNOWAIT'):raise RuntimeError('POSIX_REQUIRED')
 state={'schema':1,'operationId':operation_id,'phase':'prepared','restoreVerified':False,
        'supervisorPid':os.getpid(),'outcome':None}
 def save():record(copy.deepcopy(state))
 save()  # No worker exists if this durable intention fails.
 expected_parent=os.getpid()
 read_fd,ready_fd=os.pipe();go_fd,write_fd=os.pipe()
 try:pid=os.fork()
 except BaseException:
  for fd in (read_fd,ready_fd,go_fd,write_fd):os.close(fd)
  raise
 if pid==0:
  os.close(read_fd);os.close(write_fd)
  try:
   if not guard_parent(expected_parent):os._exit(1)
   os.setsid();os.write(ready_fd,b'R');os.close(ready_fd)
   if os.read(go_fd,1)!=b'G':os._exit(1)
   os.close(go_fd);worker();os._exit(0)
  except BaseException:os._exit(1)
 os.close(ready_fd);os.close(go_fd)
 wait_status=None;outcome='failed';record_error=None;group_ready=False
 try:
  if not select.select([read_fd],[],[],5)[0] or os.read(read_fd,1)!=b'R':
   raise RuntimeError('WORKER_NOT_READY')
  group_ready=True
  state.update(phase='running',workerPid=pid)
  save()  # The child waits for GO until this owner record is durable.
  os.write(write_fd,b'G');os.close(write_fd);write_fd=None
  deadline=time.monotonic()+timeout
  while True:
   wait_status=os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
   if wait_status is not None:
    outcome='completed' if wait_status.si_code==os.CLD_EXITED and wait_status.si_status==0 else 'failed'
    break
   if time.monotonic()>=deadline:outcome='timed_out';break
   time.sleep(.02)
 except Exception as error:
  record_error=error
 finally:
  os.close(read_fd)
  if write_fd is not None:os.close(write_fd)
  # Keep the leader unreaped: its PID/PGID cannot be reused while we drain it.
  stopped=None
  try:
   try:
    if group_ready:os.killpg(pid,signal.SIGKILL)
    else:os.kill(pid,signal.SIGKILL)
   except ProcessLookupError:pass
   stop_deadline=time.monotonic()+5
   while True:
    stopped=os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
    if stopped is not None and (not group_ready or not _live_group(pid)):break
    if time.monotonic()>=stop_deadline:raise RuntimeError('WORKER_GROUP_ALIVE')
    time.sleep(.02)
  except Exception:
   if stopped is not None:os.waitpid(pid,0)
   state.update(phase='owner_not_stopped',outcome=outcome)
   save()
   raise RuntimeError('WORKER_STOP_UNCONFIRMED') from None
  os.waitpid(pid,0)
 state.update(phase='restoring',outcome=outcome)
 try:save()
 except Exception as error:record_error=record_error or error
 try:state['restoreVerified']=recover() is True
 except Exception:state['restoreVerified']=False
 state['phase']='closed' if state['restoreVerified'] and record_error is None else 'recovery_failed'
 if record_error is not None:state['reason']='RECEIPT_OR_WORKER_SETUP_FAILED'
 save()  # A failed terminal record is never silently acknowledged.
 if record_error is not None:raise record_error
 return state
