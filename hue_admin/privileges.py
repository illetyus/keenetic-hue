"""Fail-closed service privilege boundary and root-owned runtime projections."""
import os,ctypes
from pathlib import Path
import re,json,hashlib,tempfile,stat

def load_tunnel_identity(root):
 return load_service_identity(root,'hue-tunnel')

def load_service_identity(root,service):
 from .service_manager import private_bytes
 profile=Path(root)/'state/service-identities.json'
 if os.name=='posix' and profile.lstat().st_uid!=0:raise ValueError('TUNNEL_IDENTITY_OWNER')
 profiles=json.loads(private_bytes(profile,4096))
 if not isinstance(profiles,dict) or set(profiles)!={'hue-tunnel','hue-agent'}:raise ValueError('TUNNEL_IDENTITY_PROFILE')
 if service not in profiles:raise ValueError('SERVICE_IDENTITY_NAME')
 for value in profiles.values():
  if (not isinstance(value,dict) or set(value)!={'schema','uid','gid'} or type(value['schema']) is not int or value['schema']!=1
      or any(type(value[k]) is not int or not 1<=value[k]<=2147483647 for k in ('uid','gid'))):
   raise ValueError('SERVICE_IDENTITY_PROFILE')
 if any(len({value[key] for value in profiles.values()})!=2 for key in ('uid','gid')):
  raise ValueError('SERVICE_IDENTITY_SHARED')
 return dict(profiles[service])

def runtime_paths(root,release_id):
 if not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',release_id):raise ValueError('TUNNEL_RELEASE_ID')
 root=Path(root).absolute();base=root.with_name(root.name+'-runtime')
 return {'base':base,'binary':base/'bin'/release_id/'cloudflared','credentials':base/'credentials',
         'token':base/'credentials/tunnel.token','data':base/'data','log':base/'data/tunnel.log'}

def safe_directory(path,mode,uid=0,gid=0):
 path=Path(path)
 if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('PROJECTION_SYMLINK')
 try:path.mkdir(mode=mode)
 except FileExistsError:pass
 else:
  os.chown(path,uid,gid);path.chmod(mode)
 info=path.stat()
 if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or stat.S_IMODE(info.st_mode)!=mode or info.st_gid!=gid:
  raise ValueError('PROJECTION_DIRECTORY_OWNER')

def projected_file(target,data,mode,gid):
 if target.is_symlink():raise ValueError('PROJECTION_FILE_SYMLINK')
 if target.exists():
  s=target.stat()
  if not stat.S_ISREG(s.st_mode) or s.st_uid!=0 or s.st_gid!=gid or stat.S_IMODE(s.st_mode)!=mode:raise ValueError('PROJECTION_FILE_OWNER')
  if target.read_bytes()==data:return
 fd,name=tempfile.mkstemp(prefix='.projection-',dir=target.parent)
 try:
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chown(name,0,gid);os.chmod(name,mode);os.replace(name,target)
 finally:
  if os.path.exists(name):os.unlink(name)

def prepare_tunnel_runtime(root,release_id):
 from .service_manager import private_bytes
 root=Path(root);identity=load_tunnel_identity(root);uid,gid=identity['uid'],identity['gid'];paths=runtime_paths(root,release_id)
 if os.geteuid()!=0:raise ValueError('ROOT_PREPARATION_REQUIRED')
 safe_directory(paths['base'],0o755)
 safe_directory(paths['base']/'bin',0o755);safe_directory(paths['binary'].parent,0o755)
 # Existing private master root remains700. Only one verified executable is projected.
 source=root/'releases'/release_id/'bin/cloudflared'
 manifest=json.loads(private_bytes(source.parent.parent/'verified-manifest.json',128*1024))
 entry=manifest['files']['bin/cloudflared']
 if source.is_symlink():raise ValueError('PROJECTION_SOURCE_SYMLINK')
 data=source.read_bytes()
 if len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('PROJECTION_BINARY_HASH')
 projected_file(paths['binary'],data,0o555,0)
 if not paths['credentials'].exists():paths['credentials'].mkdir(mode=0o700);os.chown(paths['credentials'],0,gid);paths['credentials'].chmod(0o710)
 safe_directory(paths['credentials'],0o710,0,gid)
 projected_file(paths['token'],private_bytes(root/'secrets/tunnel.token',8192),0o440,gid)
 if not paths['data'].exists():paths['data'].mkdir(mode=0o700);os.chown(paths['data'],uid,gid)
 safe_directory(paths['data'],0o700,uid,gid)
 return identity,paths

def tunnel_environment(runtime,ca_file):
 return {'PATH':'/opt/bin:/opt/sbin:/usr/bin:/bin','HOME':str(runtime),'LANG':'C','GOMAXPROCS':'1','SSL_CERT_FILE':str(ca_file)}

def tunnel_arguments(token_file,log_file):
 return ['tunnel','--no-autoupdate','--protocol','quic','--edge-ip-version','4','--metrics','127.0.0.1:18790',
         '--loglevel','warn','--logfile',str(log_file),'run','--token-file',str(token_file)]

def process_matches_identity(status,uid,gid,*,require_empty_bounding=True):
 if type(uid) is not int or type(gid) is not int or uid<=0 or gid<=0:return False
 rows=dict(l.split(':',1) for l in status.splitlines() if ':' in l)
 try:
  bounding=int(rows['CapBnd'].strip(),16)
  return (list(map(int,rows['Uid'].split()))==[uid]*4 and list(map(int,rows['Gid'].split()))==[gid]*4
          and all(k in rows for k in ('CapEff','CapPrm'))
          and all(int(rows.get(k,'0').strip(),16)==0 for k in ('CapEff','CapPrm','CapAmb','CapInh'))
          and (bounding==0 if require_empty_bounding else bounding>=0))
 except (ValueError,KeyError):return False

def no_new_privs():
 libc=ctypes.CDLL(None,use_errno=True)
 return libc.prctl(38,1,0,0,0)==0 and libc.prctl(39,0,0,0,0)==1

def capabilities_empty():
 rows=dict(l.split(':',1) for l in Path('/proc/self/status').read_text().splitlines() if ':' in l)
 if any(k not in rows for k in ('CapEff','CapPrm','CapBnd')):return False
 try:return all(int(rows.get(k,'0').strip(),16)==0 for k in ('CapEff','CapPrm','CapAmb','CapInh','CapBnd'))
 except ValueError:return False

def drop_capability_bounding_set(prctl=None,read_last=None):
 """Irreversibly constrain this service child before losing CAP_SETPCAP."""
 try:
  value=(read_last or (lambda:Path('/proc/sys/kernel/cap_last_cap').read_text()))().strip()
  if not re.fullmatch(r'[0-9]{1,2}',value) or int(value)>63:return False
  last=int(value)
  if prctl is None:
   libc=ctypes.CDLL(None,use_errno=True)
   prctl=libc.prctl
   prctl.argtypes=[ctypes.c_int,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong,ctypes.c_ulong]
   prctl.restype=ctypes.c_int
  for capability in range(last+1):
   if prctl(24,capability,0,0,0)!=0:return False
  return all(prctl(23,capability,0,0,0)==0 for capability in range(last+1))
 except (OSError,ValueError,AttributeError):return False

def drop_identity(uid,gid,ops=os,enable_nnp=no_new_privs,check_caps=capabilities_empty,drop_bounding=drop_capability_bounding_set):
 if type(uid) is not int or type(gid) is not int or not 1<=uid<=2147483647 or not 1<=gid<=2147483647:
  raise ValueError('NONROOT_ID_REQUIRED')
 if not drop_bounding():raise RuntimeError('BOUNDING_SET_DROP_UNCONFIRMED')
 ops.setgroups([]);ops.setresgid(gid,gid,gid);ops.setresuid(uid,uid,uid)
 if ops.getresuid()!=(uid,uid,uid) or ops.getresgid()!=(gid,gid,gid) or ops.getgroups():
  raise RuntimeError('IDENTITY_DROP_UNCONFIRMED')
 if not enable_nnp():raise RuntimeError('NO_NEW_PRIVS_UNCONFIRMED')
 if not check_caps():raise RuntimeError('CAPABILITIES_REMAIN')
