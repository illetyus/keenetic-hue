"""Build a code-only signed candidate; no credentials/configuration are collected."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import stat
import subprocess

ROOT=Path(__file__).resolve().parents[1]
PRIVATE_KEY_PEM_MARKERS=(
    b'-----BEGIN PRIVATE KEY-----',
    b'-----BEGIN ENCRYPTED PRIVATE KEY-----',
    b'-----BEGIN RSA PRIVATE KEY-----',
    b'-----BEGIN EC PRIVATE KEY-----',
    b'-----BEGIN DSA PRIVATE KEY-----',
    b'-----BEGIN OPENSSH PRIVATE KEY-----',
)
sys.path.insert(0,str(ROOT))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat,PrivateFormat,NoEncryption
from hue_admin.releases import build_release
from hue_admin.snapshot import read_private_bytes


def guard_private_path(path,windows=None):
    path=Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in path.parents):
        raise ValueError('BUILD_KEY_PATH')
    windows=os.name=='nt' if windows is None else windows
    if windows:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass',
                               '-File',str(ROOT/'deploy/check-private-path.ps1'),'-Path',str(path)],
                              shell=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        if result.returncode:raise ValueError('BUILD_KEY_PRIVATE_ACL_REQUIRED')
    else:
        info=path.stat()
        if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
            raise ValueError('BUILD_KEY_PRIVATE_PERMISSIONS_REQUIRED')


def collect(tunnel_binary):
    lock=json.loads((ROOT/'requirements-router.lock.json').read_text(encoding='utf-8'))
    files={}
    for package,prefix in (('hue_agent','agent'),('hue_admin','manager')):
        for path in sorted((ROOT/package).glob('*.py')):
            if path.is_symlink():raise ValueError('BUILD_SOURCE_SYMLINK')
            files[f'{prefix}/{package}/{path.name}']=path.read_text(encoding='utf-8').encode('utf-8')
    # Bootstrap and service files remain explicit, never a recursive deploy/ copy.
    names=('huectl','S97keenetic-hue','hue-supervise.sh','component-child.py','hue-usb-event','acceptance-entry.py')
    for name in names:
        path=ROOT/'deploy'/name
        if path.is_symlink():raise ValueError('BUILD_SOURCE_SYMLINK')
        files['service/'+name]=path.read_text(encoding='utf-8').encode('utf-8')
    files['manager/huectl-entry.py']=(ROOT/'deploy/huectl-entry.py').read_text(encoding='utf-8').encode('utf-8')
    files['manager/platform-lock.json']=(ROOT/'requirements-router.lock.json').read_text(encoding='utf-8').encode('utf-8')
    binary=Path(tunnel_binary)
    if binary.is_symlink() or not binary.is_file():raise ValueError('BUILD_BINARY')
    expected=lock['tunnelBinary']
    if binary.stat().st_size!=expected['size']:raise ValueError('BUILD_BINARY_SIZE')
    data=binary.read_bytes()
    if hashlib.sha256(data).hexdigest()!=expected['sha256']:raise ValueError('BUILD_BINARY_HASH')
    files['bin/cloudflared']=data
    files['metadata/requirements-router.lock.json']=(ROOT/'requirements-router.lock.json').read_text(encoding='utf-8').encode('utf-8')
    files['metadata/state-schema.json']=b'{"schema":1,"read":[1],"write":1}'
    # Part of the signed manifest: new code must never select a legacy root contract.
    files['metadata/runtime-policy.json']=json.dumps({
        'schema':1,'agent':'dedicated','tunnel':'dedicated',
        'childIdentity':True,'kernel':False},sort_keys=True).encode('utf-8')
    if any(marker in data for data in files.values() for marker in PRIVATE_KEY_PEM_MARKERS):
        raise ValueError('BUILD_PRIVATE_KEY_MATERIAL')
    return files,lock


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('keygen');p.add_argument('--private-key',required=True,type=Path);p.add_argument('--public-key',required=True,type=Path)
    p=sub.add_parser('build');p.add_argument('--signing-key',required=True,type=Path);p.add_argument('--tunnel-binary',required=True,type=Path)
    p.add_argument('--release-id',required=True);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--release-sequence',required=True,type=int)
    args=parser.parse_args();os.umask(0o077)
    if args.action=='keygen':
        if args.private_key.absolute()==args.public_key.absolute():raise ValueError('BUILD_KEY_PATHS_MUST_DIFFER')
        if args.private_key.exists() or args.public_key.exists() or args.private_key.is_symlink() or args.public_key.is_symlink():
            raise ValueError('BUILD_KEY_EXISTS')
        guard_private_path(args.private_key.parent)
        key=Ed25519PrivateKey.generate()
        for path,data in ((args.private_key,key.private_bytes(Encoding.Raw,PrivateFormat.Raw,NoEncryption())),
                          (args.public_key,key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw))):
            if any(p.is_symlink() for p in path.absolute().parents):raise ValueError('BUILD_KEY_PARENT')
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as out:out.write(data)
        print(json.dumps({'publicKeySha256':hashlib.sha256(args.public_key.read_bytes()).hexdigest()}));return
    guard_private_path(args.signing_key.parent);guard_private_path(args.signing_key)
    key=Ed25519PrivateKey.from_private_bytes(read_private_bytes(args.signing_key,32))
    files,lock=collect(args.tunnel_binary)
    deps={**lock['pythonDependencies'],**{'opkg.'+n:v for n,v in lock['nativeDependencies'].items()}}
    build_release(files,args.output,key,args.release_id,lock['architecture'],lock['pythonAbi'],deps,release_sequence=args.release_sequence)
    print(json.dumps({'releaseId':args.release_id,'files':len(files),'archiveSha256':hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__=='__main__':
    try:main()
    except Exception:
        print('RELEASE_BUILD_FAILED: check code allowlist, lock and private key paths',file=sys.stderr);raise SystemExit(1)
