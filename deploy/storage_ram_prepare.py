"""Provision a private RAM maintenance bundle over existing strict-key SSH.

The provisioner only prepares. Its root-private RAM entry additionally supports
explicit start/status; provisioning never invokes start. The input tool payload
must come from the pinned IPK builder.
Baseline Python, BusyBox and supporting libraries are trusted installed bytes.
Fill .storage-ram.local.json using deploy/storage-ram.example.json first.
See docs/storage-maintenance.md; never put private-key contents in settings.
"""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import uuid

TOOLS = {'e2fsck', 'libblkid.so.1.1.0', 'libcom_err.so.0.0', 'libe2p.so.2.3',
         'libext2fs.so.2.4', 'libss.so.2.0'}
PINNED_TOOL_SHA256 = {
    'e2fsck': 'fe3175c0d4b77ec4e098ce6379b38a91655c58da9dbbd76e4c8936612254adfb',
    'libblkid.so.1.1.0': 'c58756e21f9222a86714d39dd1bdb8ef3941e3840cb830c61b9d994b3e9635ce',
    'libcom_err.so.0.0': 'db961c6e997904b463cf56497405885a0ac11836c148f40c25fcd09c53a98d24',
    'libe2p.so.2.3': '00c1333a3f45453e3b1744c98abd14087cf13b65d63b84d18614e74e39be654a',
    'libext2fs.so.2.4': '5a6c502c099d24699c5ca7aa66e73e076f17c1e8b0dcec6a8a697318d26739b2',
    'libss.so.2.0': 'e894e4f137ede4826d5f63f234229756533fd932c7ef79f74a1df18e0b60d52e',
}
LINKS = {'libblkid.so.1': 'libblkid.so.1.1.0', 'libcom_err.so.0': 'libcom_err.so.0.0',
         'libe2p.so.2': 'libe2p.so.2.3', 'libext2fs.so.2': 'libext2fs.so.2.4', 'libss.so.2': 'libss.so.2.0'}


def build_payload(sources, tools):
    if (set(PINNED_TOOL_SHA256) != TOOLS
            or not isinstance(sources, dict) or not 1 <= len(sources) <= 256
            or any(type(k) is not str or not re.fullmatch(r'hue_admin/[A-Za-z_][A-Za-z0-9_]*\.py', k)
                   or type(v) is not bytes or len(v) > 1024*1024 for k,v in sources.items())
            or not isinstance(tools, dict) or set(tools) != {'files','links'}
            or not isinstance(tools['files'],dict) or set(tools['files']) != TOOLS | {'mke2fs'}
            or tools['links'] != LINKS):
        raise ValueError('STORAGE_PREPARATION_PAYLOAD')
    result = {'schema':1, 'sources':{}, 'tools':{'files':{},'links':dict(LINKS)}}
    for name, data in sources.items():
        result['sources'][name] = {'data':base64.b64encode(data).decode(),
                                   'sha256':hashlib.sha256(data).hexdigest()}
    for name in sorted(TOOLS):
        data = base64.b64decode(tools['files'][name], validate=True)
        if not 0 < len(data) <= 16*1024*1024: raise ValueError('STORAGE_PREPARATION_TOOL')
        if hashlib.sha256(data).hexdigest() != PINNED_TOOL_SHA256[name]:
            raise ValueError('STORAGE_PREPARATION_TOOL_HASH')
        result['tools']['files'][name] = {'data':base64.b64encode(data).decode(),
                                         'sha256':hashlib.sha256(data).hexdigest()}
    return result


def dispatch_args(action, operation_id=None):
    if action == 'prepare' and operation_id is None:return []
    if action == 'start' and operation_id is None:return ['start']
    if action == 'status' and type(operation_id) is str:
        try:valid=str(uuid.UUID(operation_id))==operation_id
        except ValueError:valid=False
        if valid:return ['status',operation_id]
    raise ValueError('STORAGE_PREPARATION_ACTION')


ENTRY = r'''
import hashlib,json,os,stat,sys
from pathlib import Path
root=Path(__file__).resolve().parent
os.chdir(root);sys.dont_write_bytecode=True
manifest=json.loads((root/'bundle-lock.json').read_text())
files=manifest['files'];links=manifest['links']
actual=set()
for path in root.rglob('*'):
 name=path.relative_to(root).as_posix()
 if path.is_symlink():
  if name not in links or path.lstat().st_uid!=0 or os.readlink(path)!=links[name]:raise RuntimeError('BUNDLE_LINK')
  actual.add(name)
 elif path.is_dir():
  info=path.stat()
  if info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o700:raise RuntimeError('BUNDLE_DIRECTORY')
 else:
  info=path.lstat()
  if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)&0o077:raise RuntimeError('BUNDLE_FILE')
  if name=='bundle-lock.json':continue
  if name not in files or hashlib.sha256(path.read_bytes()).hexdigest()!=files[name]:raise RuntimeError('BUNDLE_HASH')
  actual.add(name)
if actual!=set(files)|set(links):raise RuntimeError('BUNDLE_INVENTORY')
from hue_admin.storage_check import Runtime
from hue_admin.storage_commands import RamCommands
from hue_admin.storage_operation import dispatch
runtime=Runtime(root/'runtime',json.loads((root/'runtime-lock.json').read_text()))
lock=json.loads((root/'commands-lock.json').read_text())
factory=lambda node,storage:RamCommands(str(root/'commands/busybox'),str(root/'commands/lib'),lock,node,storage)
value=dispatch(sys.argv[1:],runtime,factory)
if 'operationId' in value:
 value['statusCommand']=str(root/'python/lib/ld.so.1')+' --library-path '+str(root/'python/lib')+' '+str(root/'python/python3.13')+' -S '+str(root/'prepare.py')+' status '+value['operationId']
print(json.dumps({**value,'bundle':str(root),'files':len(files)}),flush=True)
'''


PROVISION = r'''
import base64,gzip,hashlib,json,os,shutil,stat,subprocess,sys,tempfile
from pathlib import Path
if os.geteuid()!=0:raise RuntimeError('ROOT_REQUIRED')
os.umask(0o077);sys.dont_write_bytecode=True
payload=json.loads(gzip.decompress(base64.b64decode(__ENCODED_PAYLOAD__,validate=True)))
if payload['schema']!=1:raise RuntimeError('PAYLOAD_SCHEMA')
if shutil.disk_usage('/tmp').free<48*1024*1024:raise RuntimeError('RAM_SPACE_REQUIRED')
root=Path(tempfile.mkdtemp(prefix='hue-storage-maint-',dir='/tmp'));root.chmod(0o700)
def copied(source,target,mode=0o600):
 source=Path(source);data=source.read_bytes();target.parent.mkdir(parents=True,exist_ok=True)
 target.write_bytes(data);target.chmod(mode)
 if hashlib.sha256(source.read_bytes()).digest()!=hashlib.sha256(target.read_bytes()).digest():raise RuntimeError('SOURCE_CHANGED')
def packed(value,target,mode=0o600,expected_hash=None):
 data=base64.b64decode(value['data'],validate=True)
 digest=hashlib.sha256(data).hexdigest()
 if digest!=value['sha256']:raise RuntimeError('PAYLOAD_HASH')
 if expected_hash is not None and digest!=expected_hash:raise RuntimeError('TOOL_HASH')
 target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);target.chmod(mode)
try:
 if set(TOOL_SHA256)!=TOOLS:raise RuntimeError('TOOL_PIN_INVENTORY')
 if set(payload['tools']['files'])!=TOOLS or payload['tools']['links']!=LINKS:raise RuntimeError('TOOL_INVENTORY')
 for name,value in payload['sources'].items():
  import re
  if not re.fullmatch(r'hue_admin/[A-Za-z_][A-Za-z0-9_]*\.py',name):raise RuntimeError('SOURCE_PATH')
  packed(value,root/name)
 runtime=root/'runtime';runtime.mkdir(mode=0o700)
 for name,value in payload['tools']['files'].items():
  if name not in TOOLS:raise RuntimeError('TOOL_PATH')
  packed(value,runtime/name,0o700 if name=='e2fsck' else 0o600,TOOL_SHA256[name])
 for name,target in LINKS.items():(runtime/name).symlink_to(target)
 pyhome=root/'python';lib=pyhome/'lib';lib.mkdir(parents=True);std=lib/'python3.13';std.mkdir()
 for name in ('ld.so.1','libc.so.6','libm.so.6','libgcc_s.so.1','libpthread.so.0','librt.so.1','libdl.so.2',
              'libutil.so.1','libpython3.13.so.1.0','libatomic.so.1','libuuid.so.1','libssp.so.0',
              'libcrypto.so.3','libssl.so.3','libffi.so.8','libz.so','libz.so.1','libz.so.1.3.1',
              'libexpat.so.1','libbz2.so.1.0','liblzma.so.5'):
  source=Path('/opt/lib')/name
  if source.is_file():copied(source,lib/name,0o700 if name=='ld.so.1' else 0o600)
 copied('/opt/bin/python3.13',pyhome/'python3.13',0o700)
 origin=Path('/opt/lib/python3.13')
 for pattern in ('*.py','*.pyc'):
  for source in origin.glob(pattern):
   if source.is_file():copied(source,std/source.name)
 for name in ('encodings','importlib','lib-dynload','collections','re','json','pathlib','urllib','asyncio',
              'concurrent','logging','email','http','xml','ctypes','multiprocessing'):
  source=origin/name
  if source.is_dir():
   for path in source.rglob('*'):
    if path.is_file() and '__pycache__' not in path.parts:copied(path,std/name/path.relative_to(source))
 # Checker uses a separate exact flat inventory, without Python or formatter.
 for name in ('ld.so.1','libc.so.6','libpthread.so.0','librt.so.1','libdl.so.2','libssp.so.0','libuuid.so.1','libgcc_s.so.1','libm.so.6'):
  if (lib/name).is_file():copied(lib/name,runtime/name,0o700 if name=='ld.so.1' else 0o600)
 lock={'schema':1,'toolVersion':'1.47.3','files':{},'links':dict(LINKS)}
 for path in runtime.iterdir():
  if not path.is_symlink():lock['files'][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
 (root/'runtime-lock.json').write_text(json.dumps(lock))
 commands=root/'commands';commandlib=commands/'lib';commandlib.mkdir(parents=True)
 copied('/opt/bin/busybox',commands/'busybox',0o700)
 for path in lib.iterdir():
  if path.is_file():copied(path,commandlib/path.name,0o700 if path.name=='ld.so.1' else 0o600)
 commandfiles={'busybox':hashlib.sha256((commands/'busybox').read_bytes()).hexdigest()}
 commandfiles.update({'lib/'+p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in commandlib.iterdir()})
 (root/'commands-lock.json').write_text(json.dumps({'files':commandfiles,'links':{}}))
 (root/'prepare.py').write_text(ENTRY)
 manifest={'files':{},'links':{}}
 for path in root.rglob('*'):
  name=path.relative_to(root).as_posix()
  if path.is_symlink():manifest['links'][name]=os.readlink(path)
  elif path.is_file():manifest['files'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
 (root/'bundle-lock.json').write_text(json.dumps(manifest,sort_keys=True))
 env={'PYTHONHOME':str(pyhome),'PYTHONDONTWRITEBYTECODE':'1','PATH':'/usr/bin:/bin','LC_ALL':'C',
      'PYTHONCOERCECLOCALE':'0','PYTHONUTF8':'1','TMPDIR':'/tmp','LD_LIBRARY_PATH':str(lib)}
 result=subprocess.run([str(lib/'ld.so.1'),'--library-path',str(lib),str(pyhome/'python3.13'),'-S',str(root/'prepare.py'),*ACTION],
       cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
 print(result.stdout.decode(errors='replace'),end='',flush=True)
 if result.returncode:raise RuntimeError('RAM_PREPARATION_FAILED')
except BaseException:
 shutil.rmtree(root)
 raise
'''


def remote_script(payload,action,operation_id=None):
    encoded=base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()
    arguments=dispatch_args(action,operation_id)
    return ('TOOLS='+repr(TOOLS)+'\nTOOL_SHA256='+repr(PINNED_TOOL_SHA256)+'\nLINKS='+repr(LINKS)+'\nENTRY='+repr(ENTRY)+'\nACTION='+repr(arguments)+'\n'
            +PROVISION.replace('__ENCODED_PAYLOAD__',repr(encoded)))


def load_settings(path):
    """Require user-supplied connection details before any payload or SSH work."""
    path=Path(path).expanduser().absolute()
    try:
        with path.open('rb') as source:raw=source.read(16385)
        if len(raw)>16384:raise ValueError()
        value=json.loads(raw.decode('utf-8-sig'))
    except (OSError,UnicodeError,ValueError):
        raise ValueError('STORAGE_SETTINGS_FILE: copy the example and fill your local settings') from None
    fields={'ssh_host','ssh_port','ssh_user','ssh_key_path'}
    if not isinstance(value,dict) or set(value)!=fields:
        raise ValueError('STORAGE_SETTINGS_FIELDS: ssh_host, ssh_port, ssh_user, ssh_key_path required')
    host=value['ssh_host'];user=value['ssh_user'];port=value['ssh_port'];key=value['ssh_key_path']
    if not isinstance(host,str) or not re.fullmatch(r'[A-Za-z0-9:][A-Za-z0-9.:%_-]{0,252}',host):
        raise ValueError('STORAGE_SETTINGS_SSH_HOST: fill your router IP address or hostname')
    if not isinstance(user,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}',user):
        raise ValueError('STORAGE_SETTINGS_SSH_USER: fill your Entware SSH username')
    if type(port) is not int or not 1<=port<=65535:
        raise ValueError('STORAGE_SETTINGS_SSH_PORT: fill an integer from 1 to 65535')
    if not isinstance(key,str) or not key.strip() or '\x00' in key:
        raise ValueError('STORAGE_SETTINGS_SSH_KEY_PATH: fill an existing private-key file path')
    try:
        key=Path(key).expanduser()
        if not key.is_absolute():key=path.parent/key
        key=key.absolute()
        if not key.is_file():raise ValueError()
    except (OSError,ValueError):
        raise ValueError('STORAGE_SETTINGS_SSH_KEY_PATH: fill an existing private-key file path') from None
    return {'ssh_host':host,'ssh_port':port,'ssh_user':user,'ssh_key_path':key}


def ssh_command(settings):
    return ['ssh','-T','-p',str(settings['ssh_port']),'-i',str(settings['ssh_key_path']),
            '-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes',
            '-o','ConnectTimeout=8',settings['ssh_user']+'@'+settings['ssh_host'],'python3 -']


def main():
    import argparse,subprocess
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool_payload',type=Path)
    parser.add_argument('--action',choices=('prepare','start','status'),default='prepare')
    parser.add_argument('--operation-id')
    repo=Path(__file__).resolve().parents[1]
    parser.add_argument('--settings',type=Path,default=repo/'.storage-ram.local.json',
                        help='Filled local JSON settings; see deploy/storage-ram.example.json')
    parser.add_argument('--log-file',type=Path,help='Optional local output log; keep it out of Git')
    args=parser.parse_args()
    try:settings=load_settings(args.settings)
    except ValueError as error:parser.error(str(error))
    dispatch_args(args.action,args.operation_id)
    sources={'hue_admin/'+p.name:p.read_bytes() for p in (repo/'hue_admin').glob('*.py')}
    payload=build_payload(sources,json.loads(args.tool_payload.read_text()))
    command=ssh_command(settings)
    result=subprocess.run(command,input=remote_script(payload,args.action,args.operation_id).encode(),stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,timeout=150)
    if args.log_file:args.log_file.write_bytes(result.stdout)
    print(result.stdout.decode(errors='replace'),end='')
    return result.returncode


if __name__=='__main__':raise SystemExit(main())
