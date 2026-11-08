"""Trusted standalone installer/recovery entry. Run with python3 -I.

Keep this file and the release public-key fingerprint separately from the USB.
No archive code is imported until its Ed25519 signature and code hashes pass.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import tempfile
import zipfile

ROOT=Path('/opt/var/lib/keenetic-hue-system')


def preflight_zip(stream):
    """Bound central-directory work before Python materializes ZipInfo objects."""
    previous=stream.tell()
    try:
        stream.seek(0,2);size=stream.tell()
        if size>64*1024*1024 or size<22:raise ValueError('RELEASE_SIZE')
        stream.seek(max(0,size-65557));tail=stream.read(65557)
        end=tail.rfind(b'PK\x05\x06')
        if end<0 or end+22>len(tail):raise ValueError('ZIP_END')
        if end>=20 and tail[end-20:end-16]==b'PK\x06\x07':raise ValueError('ZIP64_FORBIDDEN')
        sig,disk,cd_disk,on_disk,count,cd_size,offset,comment=struct.unpack('<4s4H2IH',tail[end:end+22])
        absolute=size-len(tail)+end
        if (end+22+comment!=len(tail) or disk or cd_disk or count!=on_disk
                or not 2<=count<=257 or cd_size>256*1024 or offset+cd_size!=absolute):
            raise ValueError('ZIP_DIRECTORY_LIMIT')
        stream.seek(offset);directory=stream.read(cd_size)
        pos=0;actual=0
        while pos<len(directory):
            if pos+46>len(directory) or directory[pos:pos+4]!=b'PK\x01\x02':raise ValueError('ZIP_DIRECTORY')
            compressed,expanded=struct.unpack_from('<II',directory,pos+20)
            name,extra,notes,disk_start=struct.unpack_from('<4H',directory,pos+28)
            local=struct.unpack_from('<I',directory,pos+42)[0]
            if disk_start or max(compressed,expanded,local)==0xffffffff:raise ValueError('ZIP64_FORBIDDEN')
            pos+=46+name+extra+notes;actual+=1
            if pos>len(directory) or actual>257:raise ValueError('ZIP_DIRECTORY_LIMIT')
        if actual!=count:raise ValueError('ZIP_COUNT')
    finally:stream.seek(previous)


def bounded_manifest_read(archive):
    info=archive.getinfo('manifest.json')
    if info.file_size>128*1024:raise ValueError('RELEASE_MANIFEST_SIZE')
    with archive.open(info) as source:raw=source.read(128*1024+1)
    if len(raw)>128*1024 or len(raw)!=info.file_size:raise ValueError('RELEASE_MANIFEST_SIZE')
    return raw


def unique(pairs):
    value={}
    for k,v in pairs:
        if k in value:raise ValueError('DUPLICATE_KEY')
        value[k]=v
    return value


def regular_file(path,limit):
    path=Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('INPUT_SYMLINK')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:raise ValueError('INPUT_TYPE_OR_SIZE')
        stream=os.fdopen(fd,'rb');fd=None;return stream
    finally:
        if fd is not None:os.close(fd)


@contextmanager
def authenticated_manager(archive,key):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    with regular_file(archive,64*1024*1024) as source:
        preflight_zip(source)
        with zipfile.ZipFile(source) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(infos)>257 or len(set(names))!=len(names):raise ValueError('ARCHIVE_ENTRIES')
            info=z.getinfo('manifest.json')
            if info.file_size>128*1024:raise ValueError('MANIFEST_SIZE')
            manifest=json.loads(bounded_manifest_read(z),object_pairs_hook=unique)
            signature=base64.b64decode(manifest.pop('signature'),validate=True)
            canonical=json.dumps(manifest,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
            Ed25519PublicKey.from_public_bytes(key).verify(signature,canonical)
            # Signed code is publisher-authorized, but extraction still obeys a tiny allowlist.
            files=manifest['files']
            if not isinstance(files,dict) or not 1<=len(files)<=256:raise ValueError('MANIFEST_FILES')
            code={};size=0
            for name,entry in files.items():
                if not name.startswith('manager/hue_admin/'):continue
                leaf=name.removeprefix('manager/hue_admin/')
                if not re.fullmatch('[a-z_]+\\.py',leaf):raise ValueError('MANAGER_PATH')
                item=z.getinfo(name)
                if type(entry['size']) is not int or not 0<=entry['size']<=1024*1024 or item.file_size!=entry['size']:
                    raise ValueError('MANAGER_SIZE')
                size+=item.file_size
                if size>4*1024*1024:raise ValueError('MANAGER_BUDGET')
                data=z.read(item)
                if hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('MANAGER_HASH')
                code[leaf]=data
            if not {'__init__.py','cli.py'}.issubset(code):raise ValueError('MANAGER_MISSING')
            with tempfile.TemporaryDirectory(prefix='hue-trusted-installer-') as tmp:
                manager=Path(tmp);package=manager/'hue_admin';package.mkdir(mode=0o700)
                for name,data in code.items():
                    fd=os.open(package/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                    with os.fdopen(fd,'wb') as output:output.write(data)
                yield manager


def main(argv=None):
    sys.dont_write_bytecode=True;os.umask(0o077)
    parser=argparse.ArgumentParser(description='Install a signed inactive Keenetic Hue release and recover its stable CLI')
    parser.add_argument('archive',type=Path)
    parser.add_argument('--trust-key',required=True,type=Path)
    parser.add_argument('--trust-key-sha256',required=True)
    parser.add_argument('--migrate-existing',action='store_true',help='Move the fixed legacy installation after a healthy verified start')
    args=parser.parse_args(argv)
    if not re.fullmatch('[a-fA-F0-9]{64}',args.trust_key_sha256):raise ValueError('TRUST_PIN_REQUIRED')
    with regular_file(args.trust_key,32) as source:key=source.read(33)
    if len(key)!=32 or not hmac.compare_digest(hashlib.sha256(key).hexdigest(),args.trust_key_sha256.lower()):raise ValueError('TRUST_PIN_MISMATCH')
    with authenticated_manager(args.archive,key) as manager:
        sys.path.insert(0,str(manager))
        from hue_admin.release_commands import archive_release
        from hue_admin.installation import install_candidate
        from hue_admin.bootstrap_install import provision_bootstrap
        from hue_admin.restore_current import management_lock
        from hue_admin.migration import migrate,recover_if_needed,new_action
        release,runtime=archive_release(args.archive,key)
        release_id=json.loads(release.signed_manifest)['releaseId']
        # install_candidate pins/rechecks the anchor before bootstrap code changes.
        result=install_candidate(release,ROOT,'repair' if (ROOT/'active.json').exists() else 'fresh',
                                 stop_active=lambda:new_action('stop',ROOT))
        with management_lock(ROOT):
            provision_bootstrap(ROOT,release_id,key,runtime,Path('/opt/bin/huectl'),Path('/opt/etc/init.d/S97keenetic-hue'),locked=True)
            if args.migrate_existing:
                recover_if_needed(ROOT)
                import time
                from hue_admin.unified_health import status
                def healthy():
                    deadline=time.monotonic()+90
                    while time.monotonic()<deadline:
                        value=status(True,ROOT)
                        if value.get('healthy') and value.get('releaseId')==release_id:return value
                        time.sleep(1)
                    return {'healthy':False}
                result=migrate(ROOT,release_id,key,runtime,health=healthy)
                if result.get('alreadyMigrated') and not new_action('start',ROOT):raise RuntimeError('RECOVERY_START_FAILED')
        print(json.dumps(result));return 0


def install_error_message(error):
    # Only fixed public error codes are shown, never arbitrary exception data.
    if isinstance(error,ValueError) and error.args==('ACCOUNT_UNPROFILED_NAME',):
        return ('ACCOUNT_UNPROFILED_NAME: Servis hesaplari var, kimlik profili yok; hesaplar devralinmadi. '
                'Yeni USB/Entware kurulumunda Keenetic yeniden baslatildiktan sonra ayni imzali kurulumu yineleyin. '
                'Mevcut kurulumda profil/yedek kurtarma kilavuzunu izleyin; hesap dosyalarini elle silmeyin.')
    return 'TRUSTED_INSTALL_FAILED: preserve the archive and rerun this trusted installer to recover'


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(install_error_message(error),file=sys.stderr)
        raise SystemExit(1)
