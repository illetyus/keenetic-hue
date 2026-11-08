"""Password-encrypted, authenticated private snapshots, format 1."""
import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .manifest import KDF, MAX_BYTES, MAX_FILES, validate_identity, validate_name
from .resources import require_memory


def validate_new_backup_password(password):
    if (not isinstance(password,str) or len(password)<16
            or len(password.encode('utf-8'))>1024 or len(set(password))<4):
        raise ValueError('BACKUP_PASSWORD_POLICY')


def derive_key(password, salt):
    if not isinstance(password, str) or not 1 <= len(password.encode('utf-8')) <= 1024:
        raise ValueError('BACKUP_PASSWORD')
    return Scrypt(salt=salt, length=32, **KDF).derive(password.encode('utf-8'))


def seal(files, password, identity):
    validate_new_backup_password(password)
    validate_identity(identity)
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('BACKUP_FILES')
    size = 0
    encoded = {}
    for name, data in files.items():
        validate_name(name)
        if not isinstance(data, bytes):
            raise ValueError('BACKUP_CONTENT')
        size += len(data)
        if size > MAX_BYTES // 2:
            raise ValueError('BACKUP_TOO_LARGE')
    require_memory(size)
    encoded = {name:base64.b64encode(data).decode('ascii') for name,data in files.items()}
    payload = json.dumps({'identity': identity, 'files': encoded}, separators=(',', ':')).encode('utf-8')
    salt, nonce = os.urandom(16), os.urandom(12)
    header = json.dumps({'formatVersion': 1, 'cipher': 'AES-256-GCM', 'kdf': 'scrypt',
                         **KDF, 'salt': salt.hex(), 'nonce': nonce.hex()},
                        sort_keys=True, separators=(',', ':')).encode('ascii')
    result = header + b'\n' + AESGCM(derive_key(password, salt)).encrypt(nonce, payload, header)
    if len(result) > MAX_BYTES:
        raise ValueError('BACKUP_TOO_LARGE')
    return result
