"""Authenticate before staging. This module never replaces an installation."""
import base64
import json
import os
import shutil
import stat
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .backup import derive_key
from .manifest import KDF, MAX_BYTES, MAX_FILES, unique_object, validate_identity, validate_name
from .resources import require_memory


def open_backup(raw, password, identity):
    validate_identity(identity)
    files,authenticated_identity=decode_backup(raw,password)
    if authenticated_identity!=identity:raise ValueError('BACKUP_INVALID_OR_WRONG_PASSWORD')
    return files


def decode_backup(raw,password):
    """Authenticate the encrypted identity for a blank same-adapter installation."""
    if not isinstance(raw, bytes) or not 16 <= len(raw) <= MAX_BYTES:
        raise ValueError('BACKUP_SIZE')
    split = raw.find(b'\n', 0, 1025)
    if split < 0:
        raise ValueError('BACKUP_HEADER')
    header, ciphertext = raw[:split], raw[split+1:]
    try:
        meta = json.loads(header, object_pairs_hook=unique_object)
        if (not isinstance(meta, dict)
                or set(meta) != {'formatVersion', 'cipher', 'kdf', 'n', 'r', 'p', 'salt', 'nonce'}
                or type(meta['formatVersion']) is not int or meta['formatVersion'] != 1
                or meta['cipher'] != 'AES-256-GCM' or meta['kdf'] != 'scrypt'
                or any(type(meta[k]) is not int or meta[k] != v for k, v in KDF.items())
                or not isinstance(meta['salt'], str) or len(meta['salt']) != 32
                or not isinstance(meta['nonce'], str) or len(meta['nonce']) != 24):
            raise ValueError('BACKUP_HEADER')
        salt, nonce = bytes.fromhex(meta['salt']), bytes.fromhex(meta['nonce'])
        if salt.hex()!=meta['salt'] or nonce.hex()!=meta['nonce']:
            raise ValueError('BACKUP_HEADER')
        require_memory(len(raw))
        payload = AESGCM(derive_key(password, salt)).decrypt(nonce, ciphertext, header)
        value = json.loads(payload, object_pairs_hook=unique_object)
        if not isinstance(value, dict) or set(value) != {'identity', 'files'}:
            raise ValueError('BACKUP_IDENTITY')
        validate_identity(value['identity'])
        encoded = value['files']
        if not isinstance(encoded, dict) or not 1 <= len(encoded) <= MAX_FILES:
            raise ValueError('BACKUP_FILES')
        files = {}
        size = 0
        for name, data in encoded.items():
            validate_name(name)
            if not isinstance(data, str):
                raise ValueError('BACKUP_CONTENT')
            files[name] = base64.b64decode(data, validate=True)
            size += len(files[name])
            if size > MAX_BYTES // 2:
                raise ValueError('BACKUP_TOO_LARGE')
        return files,value['identity']
    except (InvalidTag, UnicodeError, TypeError, KeyError, OverflowError, ValueError) as exc:
        raise ValueError('BACKUP_INVALID_OR_WRONG_PASSWORD') from None


def write_private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def stage_backup(raw, password, identity, target):
    files = open_backup(raw, password, identity)
    target = Path(target).absolute()
    if target.exists() or target.is_symlink():
        raise ValueError('STAGING_EXISTS')
    if any(p.is_symlink() for p in target.parents):
        raise ValueError('STAGING_SYMLINK')
    info = target.parent.stat()
    if not stat.S_ISDIR(info.st_mode) or (sys.platform == 'linux' and (info.st_mode & 0o077 or (hasattr(os, 'geteuid') and os.geteuid() == 0 and info.st_uid != 0))):
        raise ValueError('STAGING_PRIVATE_DIRECTORY_REQUIRED')
    # Parent must be owned by the administrator; staging is never in an untrusted shared directory.
    target.mkdir(mode=0o700)
    try:
        for name, data in files.items():
            path = target / name
            path.parent.mkdir(mode=0o700, exist_ok=True)
            write_private(path, data)
    except BaseException:
        shutil.rmtree(target)
        raise
    return target
