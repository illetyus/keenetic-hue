"""Parse agent-controlled SQLite under the agent UID, not the root manager.

Only a fresh bounded output descriptor is inherited. The worker cannot name or
open root-private credentials; root receives opaque snapshot bytes. Integration
with backup/restore and runtime migration is deliberately separate.
"""
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import json
from contextlib import nullcontext,contextmanager

from .privileges import drop_identity

LIMIT=16*1024*1024
WORKER=r'''
import os,sqlite3,stat,sys,tempfile,time,json,hashlib,shutil
from pathlib import Path
phase=1
workspace=None
try:
 path=Path(sys.argv[1]);uid=int(sys.argv[2]);gid=int(sys.argv[3]);output=int(sys.argv[4]);limit=int(sys.argv[5])
 assert os.getresuid()==(uid,)*3 and os.getresgid()==(gid,)*3 and os.getgroups()==[]
 info=path.lstat()
 assert stat.S_ISREG(info.st_mode) and info.st_uid==uid and info.st_gid==gid
 assert stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1 and info.st_size<=limit
 deadline=time.monotonic()+8
 expected_recovery=json.loads(sys.argv[6])
 if expected_recovery is not None:
  phase=7
  workspace=Path(expected_recovery['workspace']);path=workspace/'commands.sqlite'
  parent=workspace.parent.lstat()
  assert parent.st_uid==0 and not stat.S_IMODE(parent.st_mode)&0o022
  assert not any(p.is_symlink() for p in (workspace,*workspace.parents))
  for label,leaf,bound,fd in (('main',path,limit,expected_recovery['backup_fds'][0]),
                            ('journal',Path(str(path)+'-journal'),2*limit,expected_recovery['backup_fds'][1])):
   before=os.fstat(fd)
   assert stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
   assert stat.S_IMODE(before.st_mode)==0o600 and before.st_size<=bound
   digest=hashlib.sha256();total=0
   with leaf.open('xb') as copied:
    os.chmod(leaf,0o600)
    while True:
     block=os.read(fd,65536)
     if not block:break
     total+=len(block);assert total<=bound and time.monotonic()<=deadline
     digest.update(block);copied.write(block)
    copied.flush();os.fsync(copied.fileno())
   assert total==expected_recovery[label]['stamp'][2]
   assert digest.hexdigest()==expected_recovery[label]['sha256']
  info=path.lstat()
 query='select type,name,tbl_name,sql from sqlite_master order by type,name'
 phase=2
 with sqlite3.connect(':memory:') as reference:
  reference.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
  reference.execute('CREATE TABLE sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)')
  expected=reference.execute(query).fetchall()
 phase=3
 with tempfile.TemporaryDirectory(prefix='.hue-snapshot-',dir=path.parent) as directory:
  target=Path(directory)/'snapshot.sqlite'
  mode='rw' if expected_recovery is not None else 'ro'
  with sqlite3.connect(path.absolute().as_uri()+'?mode='+mode,uri=True,timeout=2) as source:
   source.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
   disable=getattr(source,'enable_load_extension',None)
   if disable is not None:disable(False)
   source.execute('pragma trusted_schema=OFF')
   source.execute('pragma query_only=ON')
   if hasattr(source,'setlimit'):source.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,limit)
   phase=8 if expected_recovery is not None else 4
   assert source.execute(query).fetchall()==expected
   phase=4
   if expected_recovery is not None:
    current=path.lstat()
    assert (current.st_dev,current.st_ino)==(info.st_dev,info.st_ino)
   page_size=source.execute('pragma page_size').fetchone()[0]
   def progress(status,remaining,total):
    if time.monotonic()>deadline or total*page_size>limit:raise RuntimeError('BUDGET')
   phase=5
   with sqlite3.connect(target) as destination:
    destination.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
    destination.execute('pragma trusted_schema=OFF')
    source.backup(destination,pages=64,progress=progress,sleep=.01)
    assert destination.execute('pragma quick_check').fetchone()==('ok',)
    assert destination.execute(query).fetchall()==expected
  phase=6
  assert target.stat().st_size<=limit
  with target.open('rb') as snapshot:
   while True:
    block=snapshot.read(65536)
    if not block:break
    while block:
     count=os.write(output,block)
     if count<=0:raise OSError('OUTPUT')
     block=block[count:]
  os.fsync(output)
except BaseException:
 raise SystemExit(70+phase)
finally:
 if workspace is not None:
  def cleanup_error(function,name,error):
   # Agent may remove contents, but only root may remove the staging leaf
   # from its nonreplaceable parent. Root performs that final rmdir later.
   if function==os.rmdir and Path(name)==workspace and isinstance(error,PermissionError):return
   raise error
  shutil.rmtree(workspace,onexc=cleanup_error)
'''


@contextmanager
def _recovery_workspace(path,uid,gid,expected):
    if expected is None:
        yield None;return
    base=path.parent.parent
    info=base.lstat()
    if info.st_uid!=0 or info.st_mode&0o022:raise ValueError('AGENT_RECOVERY_STAGE_PARENT')
    parent=Path(tempfile.mkdtemp(prefix='.sqlite-recovery-',dir=base))
    os.chown(parent,0,gid);parent.chmod(0o710)
    leaf=parent/'agent';leaf.mkdir(mode=0o700);os.chown(leaf,uid,gid)
    try:
        yield dict(expected,workspace=str(leaf))
    finally:
        # No root recursion through worker-controlled entries. Leave leftovers
        # quarantined if a killed worker could not remove its private leaf.
        try:leaf.rmdir()
        except FileNotFoundError:pass
        except OSError:pass
        try:parent.rmdir()
        except OSError:pass


def snapshot_agent_database(path,uid,gid,*,output_dir,recover_hot=False):
    if any(type(value) is not int or not 1<=value<=2147483647 for value in (uid,gid)):
        raise ValueError('AGENT_SNAPSHOT_IDENTITY')
    if os.name!='posix' or os.geteuid()!=0:raise RuntimeError('AGENT_SNAPSHOT_NATIVE_ROOT_REQUIRED')
    if type(recover_hot) is not bool:raise ValueError('AGENT_RECOVERY_AUTHORIZATION')
    path=Path(path);output_dir=Path(output_dir)
    if not path.is_absolute() or not output_dir.is_absolute():raise ValueError('AGENT_SNAPSHOT_PATH')
    if output_dir.is_symlink() or any(p.is_symlink() for p in output_dir.parents):
        raise ValueError('AGENT_SNAPSHOT_OUTPUT')
    info=output_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:
        raise ValueError('AGENT_SNAPSHOT_OUTPUT')
    def restrict():
        import resource
        file_limit=2*LIMIT if recover_hot else LIMIT
        resource.setrlimit(resource.RLIMIT_FSIZE,(file_limit,file_limit))
        resource.setrlimit(resource.RLIMIT_CPU,(12,12))
        resource.setrlimit(resource.RLIMIT_AS,(64*1024*1024,64*1024*1024))
        drop_identity(uid,gid)
    from .agent_storage import preserve_agent_recovery
    preservation=preserve_agent_recovery(path,uid,gid,output_dir) if recover_hot else nullcontext(None)
    with preservation as preserved, _recovery_workspace(path,uid,gid,preserved) as expected_recovery, tempfile.TemporaryFile(dir=output_dir) as output:
        try:
            result=subprocess.run([sys.executable,'-I','-c',WORKER,str(path),str(uid),str(gid),str(output.fileno()),str(LIMIT),json.dumps(expected_recovery)],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                pass_fds=(output.fileno(),*(expected_recovery['backup_fds'] if expected_recovery else [])),preexec_fn=restrict,timeout=20,
                cwd='/',env={'PATH':'/opt/bin:/usr/bin:/bin','LANG':'C'})
        except subprocess.TimeoutExpired:
            raise RuntimeError('AGENT_DATABASE_SNAPSHOT_FAILED:TIMEOUT') from None
        except (OSError,subprocess.SubprocessError):
            raise RuntimeError('AGENT_DATABASE_SNAPSHOT_FAILED:START') from None
        if result.returncode or not 0<os.fstat(output.fileno()).st_size<=LIMIT:
            reasons={71:'IDENTITY',72:'REFERENCE',73:'OPEN',74:'SCHEMA',75:'BACKUP',76:'OUTPUT',77:'RECOVERY_INPUT',78:'RECOVERY'}
            reason=reasons.get(result.returncode,'WORKER_EXIT_'+str(result.returncode))
            raise RuntimeError('AGENT_DATABASE_SNAPSHOT_FAILED:'+reason)
        output.seek(0);data=output.read(LIMIT+1)
        if len(data)>LIMIT or not data.startswith(b'SQLite format 3\x00'):
            raise RuntimeError('AGENT_DATABASE_SNAPSHOT_FAILED')
        return data


def validate_agent_database_bytes(root,release_id,data):
    """Stage opaque restore bytes with FD ownership, then parse under the UID.

    The service can write inside the one disposable leaf, but cannot replace
    its name in the root-owned validation parent. Root never opens SQLite.
    Cleanup removes only the exact input leaf and an empty directory, never
    recursively traversing files that the service could create.
    """
    from .runtime_policy import release_policy
    from .privileges import load_service_identity
    from .agent_storage import agent_runtime_paths
    from .agent_runtime import _root_directory
    if os.name!='posix' or os.geteuid()!=0:raise RuntimeError('AGENT_VALIDATION_NATIVE_ROOT_REQUIRED')
    if not isinstance(data,bytes) or not 0<len(data)<=LIMIT or not data.startswith(b'SQLite format 3\x00'):
        raise ValueError('AGENT_VALIDATION_BYTES')
    root=Path(root).absolute()
    if release_policy(root,release_id)['agent']!='dedicated':raise ValueError('AGENT_RESTRICTED_POLICY_REQUIRED')
    identity=load_service_identity(root,'hue-agent');uid,gid=identity['uid'],identity['gid']
    paths=agent_runtime_paths(root,release_id)
    _root_directory(paths['base'].parent,0o755)
    _root_directory(paths['base'],0o710,gid)
    parent=paths['base']/'validation';_root_directory(parent,0o710,gid)
    leaf=Path(tempfile.mkdtemp(prefix='database-',dir=parent))
    directory_fd=os.open(leaf,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fd=os.open('input.sqlite',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory_fd)
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fchown(output.fileno(),uid,gid);os.fsync(output.fileno())
        os.fchown(directory_fd,uid,gid)
        return snapshot_agent_database(leaf/'input.sqlite',uid,gid,output_dir=root/'run')
    finally:
        try:
            try:os.unlink('input.sqlite',dir_fd=directory_fd)
            except FileNotFoundError:pass
        finally:os.close(directory_fd)
        leaf.rmdir()
