"""Conservative budget for small embedded devices; never lower KDF cost."""
import sys
from pathlib import Path


def require_memory(content_bytes):
    if sys.platform != 'linux':
        return  # Development fixtures; deployed target is Linux.
    try:
        lines = Path('/proc/meminfo').read_text(encoding='ascii').splitlines()
        available = next(int(line.split()[1])*1024 for line in lines if line.startswith('MemAvailable:'))
    except (OSError, ValueError, StopIteration, IndexError):
        raise ValueError('BACKUP_MEMORY_UNKNOWN') from None
    # Scrypt uses about 16 MiB; reserve additional interpreter/cipher/JSON copies.
    if available < 48*1024*1024 + content_bytes*6:
        raise ValueError('BACKUP_MEMORY_INSUFFICIENT')
