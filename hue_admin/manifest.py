"""Bounded private-backup schema; no executable archive entries."""
import re

MAX_BYTES = 32 * 1024 * 1024
MAX_FILES = 64
KDF = {'n': 16384, 'r': 8, 'p': 1}


def validate_name(name):
    if not isinstance(name, str) or len(name) > 180:
        raise ValueError('BACKUP_PATH')
    parts = name.split('/')
    if (len(parts) != 2 or parts[0] not in ('config', 'secrets', 'state')
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', parts[1])
            or parts[1] in ('.', '..') or parts[1].endswith('.')):
        raise ValueError('BACKUP_PATH')
    return name


def validate_identity(identity):
    if (not isinstance(identity, dict) or set(identity) != {'adapter', 'lamp'}
            or any(not isinstance(v, str) or not 1 <= len(v) <= 64 for v in identity.values())):
        raise ValueError('BACKUP_IDENTITY')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('BACKUP_DUPLICATE_KEY')
        result[key] = value
    return result
