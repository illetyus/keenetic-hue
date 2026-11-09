"""Signed code-only releases. Verification and extraction do not activate code."""
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .manifest import unique_object

MAX_RELEASE = 64*1024*1024
MAX_MANIFEST = 128*1024
ROOTS = {'agent','manager','bin','service','metadata'}


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


def release_name(name):
    if not isinstance(name,str) or not 1<len(name)<=240:
        raise ValueError('RELEASE_PATH')
    parts=name.split('/')
    reserved={'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
    if (len(parts)<2 or parts[0] not in ROOTS
            or any(p in ('.','..') or not re.fullmatch('[A-Za-z0-9_.-]+',p) for p in parts)
            or any(p.endswith('.') or p.split('.')[0].upper() in reserved for p in parts)
            or any(p.lower() in ('.git','__pycache__','config.json','device.json','keystore.json')
                   or p.lower().startswith('.env') or p.lower().endswith(('.token','.pem','.sqlite','.pyc')) for p in parts)):
        raise ValueError('RELEASE_PATH')
    return name


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def digest_stream(stream):
    digest=hashlib.sha256();total=0
    while chunk:=stream.read(65536):
        total+=len(chunk)
        if total>MAX_RELEASE:raise ValueError('RELEASE_SIZE')
        digest.update(chunk)
    return digest.hexdigest(),total


def open_archive(path):
    path=Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('RELEASE_SYMLINK')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_RELEASE:
            raise ValueError('RELEASE_SIZE')
        if os.name=='posix' and info.st_mode&0o022:raise ValueError('RELEASE_WRITABLE_BY_OTHERS')
        return os.fdopen(fd,'rb')
    except BaseException:
        os.close(fd);raise


@dataclass(frozen=True)
class VerifiedRelease:
    archive: Path
    archive_hash: str
    signed_manifest: bytes
    trust_key: bytes
    architecture: str
    python_abi: str
    dependencies: tuple


def build_release(files,destination,private_key,release_id,architecture,python_abi,dependencies,release_sequence=None):
    if not isinstance(files,dict) or not 1<=len(files)<=256:raise ValueError('RELEASE_FILES')
    entries={}
    for name,data in files.items():
        release_name(name)
        if not isinstance(data,bytes):raise ValueError('RELEASE_CONTENT')
        entries[name]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data),
                       'mode':0o700 if name.startswith('bin/') or name.startswith('service/') and not name.endswith('.py') else 0o600}
    manifest={'schema':1,'releaseId':release_id,'architecture':architecture,'pythonAbi':python_abi,
              'dependencies':dependencies,'files':entries}
    if release_sequence is not None:manifest['releaseSequence']=release_sequence
    validate_manifest(manifest,architecture,python_abi,dependencies)
    manifest['signature']=base64.b64encode(private_key.sign(canonical(manifest))).decode('ascii')
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as output:
            with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                manifest_info=zipfile.ZipInfo('manifest.json');manifest_info.compress_type=zipfile.ZIP_DEFLATED
                manifest_info.external_attr=(stat.S_IFREG|0o600)<<16
                archive.writestr(manifest_info,canonical(manifest))
                for name,data in files.items():
                    info=zipfile.ZipInfo(name);info.compress_type=zipfile.ZIP_DEFLATED
                    info.external_attr=(stat.S_IFREG|entries[name]['mode'])<<16
                    archive.writestr(info,data)
            output.flush();os.fsync(output.fileno())
        if Path(destination).stat().st_size>MAX_RELEASE:raise ValueError('RELEASE_SIZE')
    except BaseException:
        Path(destination).unlink(missing_ok=True);raise


def validate_manifest(value,architecture,python_abi,dependencies):
    if (not isinstance(value,dict) or set(value)-{'releaseSequence'}!={'schema','releaseId','architecture','pythonAbi','dependencies','files'}
            or type(value['schema']) is not int or value['schema']!=1
            or not isinstance(value['releaseId'],str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',value['releaseId'])
            or value['architecture']!=architecture or value['pythonAbi']!=python_abi):
        raise ValueError('RELEASE_PLATFORM_OR_SCHEMA')
    if 'releaseSequence' in value and (type(value['releaseSequence']) is not int or not 1<=value['releaseSequence']<2**53):
        raise ValueError('RELEASE_SEQUENCE')
    required=value['dependencies']
    if not isinstance(required,dict) or not required or len(required)>64:raise ValueError('RELEASE_DEPENDENCIES')
    for name,version in required.items():
        if (not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_.-]{1,80}',name)
                or not isinstance(version,str) or not re.fullmatch('[A-Za-z0-9_.+-]{1,80}',version)
                or dependencies.get(name)!=version):raise ValueError('RELEASE_DEPENDENCY_MISMATCH')
    files=value['files']
    if not isinstance(files,dict) or not 1<=len(files)<=256:raise ValueError('RELEASE_FILES')
    total=0
    for name,entry in files.items():
        release_name(name)
        if (not isinstance(entry,dict) or set(entry)!={'sha256','size','mode'}
                or not isinstance(entry['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',entry['sha256'])
                or type(entry['size']) is not int or not 0<=entry['size']<=MAX_RELEASE
                or type(entry['mode']) is not int or entry['mode'] not in (0o600,0o700)):
            raise ValueError('RELEASE_ENTRY')
        total+=entry['size']
    if total>MAX_RELEASE:raise ValueError('RELEASE_SIZE')
    # Refuse a file that is also another file's ancestor.
    for name in files:
        if any('/'.join(name.split('/')[:i]) in files for i in range(1,len(name.split('/')))):
            raise ValueError('RELEASE_PATH_CONFLICT')


def inspect_archive(stream,trust_key,architecture,python_abi,dependencies):
    try:
        preflight_zip(stream)
        with zipfile.ZipFile(stream) as archive:
            infos=archive.infolist()
            if not 2<=len(infos)<=257 or len({i.filename for i in infos})!=len(infos):raise ValueError('RELEASE_DUPLICATE')
            for info in infos:
                kind=stat.S_IFMT(info.external_attr>>16)
                if (info.filename!=info.orig_filename or info.is_dir() or info.flag_bits&1
                        or kind not in (0,stat.S_IFREG) or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED)):
                    raise ValueError('RELEASE_ENTRY_TYPE')
            info=archive.getinfo('manifest.json')
            if info.file_size>MAX_MANIFEST:raise ValueError('RELEASE_MANIFEST_SIZE')
            raw=bounded_manifest_read(archive)
            signed=verify_signed_manifest(raw,trust_key,architecture,python_abi,dependencies)
            if {i.filename for i in infos}!={'manifest.json',*signed['files']}:raise ValueError('RELEASE_UNDECLARED_FILE')
            for name,entry in signed['files'].items():
                info=archive.getinfo(name)
                if info.file_size!=entry['size']:raise ValueError('RELEASE_SIZE_MISMATCH')
                with archive.open(info) as source:digest,size=digest_stream(source)
                if digest!=entry['sha256'] or size!=entry['size']:raise ValueError('RELEASE_HASH')
            return raw,signed
    except (InvalidSignature,zipfile.BadZipFile,KeyError,TypeError,UnicodeError,ValueError,RuntimeError):
        raise ValueError('RELEASE_INVALID') from None


def verify_signed_manifest(raw,trust_key,architecture,python_abi,dependencies):
    if not isinstance(raw,bytes) or len(raw)>MAX_MANIFEST:raise ValueError('RELEASE_MANIFEST_SIZE')
    try:
        value=json.loads(raw,object_pairs_hook=unique_object)
        if not isinstance(value,dict) or 'signature' not in value:raise ValueError('RELEASE_SIGNATURE')
        signature=base64.b64decode(value.pop('signature'),validate=True)
        Ed25519PublicKey.from_public_bytes(trust_key).verify(signature,canonical(value))
        validate_manifest(value,architecture,python_abi,dependencies)
        return value
    except (InvalidSignature,ValueError,TypeError,UnicodeError):
        raise ValueError('RELEASE_INVALID') from None


def verify_release(path,trust_key,architecture,python_abi,dependencies):
    with open_archive(path) as stream:
        digest,_=digest_stream(stream);stream.seek(0)
        raw,_=inspect_archive(stream,trust_key,architecture,python_abi,dependencies)
    return VerifiedRelease(Path(path).absolute(),digest,raw,trust_key,architecture,python_abi,tuple(sorted(dependencies.items())))


def extract_release(release,target):
    target=Path(target).absolute()
    if target.exists() or target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise ValueError('RELEASE_STAGE_EXISTS_OR_SYMLINK')
    with open_archive(release.archive) as stream:
        digest,_=digest_stream(stream)
        if digest!=release.archive_hash:raise ValueError('RELEASE_CHANGED')
        stream.seek(0)
        raw,manifest=inspect_archive(stream,release.trust_key,release.architecture,release.python_abi,dict(release.dependencies))
        if raw!=release.signed_manifest:raise ValueError('RELEASE_CHANGED')
        stream.seek(0)
        target.mkdir(mode=0o700)
        try:
            with zipfile.ZipFile(stream) as archive:
                for name,entry in manifest['files'].items():
                    path=target
                    for part in name.split('/')[:-1]:
                        path/=part;path.mkdir(mode=0o700,exist_ok=True)
                    path=target/name
                    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),entry['mode'])
                    with os.fdopen(fd,'wb') as output,archive.open(name) as source:
                        digest=hashlib.sha256();size=0
                        while chunk:=source.read(65536):
                            size+=len(chunk)
                            if size>entry['size']:raise ValueError('RELEASE_SIZE_MISMATCH')
                            digest.update(chunk);output.write(chunk)
                        output.flush();os.fsync(output.fileno())
                    if size!=entry['size'] or digest.hexdigest()!=entry['sha256']:raise ValueError('RELEASE_HASH')
            (target/'verified-manifest.json').write_bytes(raw)
            (target/'verified-manifest.json').chmod(0o600)
        except BaseException:
            shutil.rmtree(target);raise
    return manifest['releaseId']
