"""Allowlisted inventory only; raw config/credential data is never exported."""
import hashlib
import os
from pathlib import Path


def redact(value):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if any(term in key.lower() for term in
                ('token', 'password', 'secret', 'keystore', 'authorization', 'private_key'))
                else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def inventory(root: Path):
    result = {'files': {}, 'platform': os.name}
    for name in ('app/launch.py', 'config.json'):
        path = root / name
        if path.is_file() and not path.is_symlink():
            result['files'][name] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                     'mode': oct(path.stat().st_mode & 0o777)}
    return result
