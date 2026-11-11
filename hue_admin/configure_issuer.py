"""Provision only the narrow setup-code authority; no Cloudflare admin key."""
import json
import os
import re
import stat
import sys
from pathlib import Path

from .restore_current import SYSTEM, management_lock
from .restore_transaction import atomic_replace
from .setup_code import request_setup_code
from .snapshot import read_private_bytes


def configure_issuer(origin, token, root=SYSTEM):
    if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{64}',token):
        raise ValueError('INVALID_ISSUER')
    with management_lock(root):
        request_setup_code(origin,token)  # Discard the code; verifies exact HTTPS origin + authority.
        root=Path(root).absolute()
        if root.is_symlink() or any(p.is_symlink() for p in root.parents):raise ValueError('ISSUER_PATH')
        if sys.platform=='linux' and root.exists():
            rinfo=root.stat()
            if not stat.S_ISDIR(rinfo.st_mode) or rinfo.st_mode&0o077 or (hasattr(os,'geteuid') and os.geteuid()==0 and rinfo.st_uid!=0):
                raise ValueError('ISSUER_PRIVATE_DIRECTORY')
        for name in ('config','secrets'):
            path=root/name
            if path.is_symlink():raise ValueError('ISSUER_PATH')
            path.mkdir(mode=0o700,exist_ok=True)
            info=path.stat()
            if not stat.S_ISDIR(info.st_mode) or (sys.platform=='linux' and (stat.S_IMODE(info.st_mode)!=0o700 or (hasattr(os,'geteuid') and os.geteuid()==0 and info.st_uid!=0))):
                raise ValueError('ISSUER_PRIVATE_DIRECTORY')
        targets={root/'secrets/setup-issuer.token':token.encode('ascii'),
                 root/'config/management.json':json.dumps({'publicOrigin':origin}).encode('utf-8')}
        before={path:read_private_bytes(path) if path.exists() else None for path in targets}
        touched=[]
        try:
            for path,data in targets.items():
                if path.is_symlink():raise ValueError('ISSUER_PATH')
                atomic_replace(path,data);touched.append(path)
        except Exception:
            for path in reversed(touched):
                if before[path] is None:path.unlink()
                else:atomic_replace(path,before[path])
            raise
    return {'configured':True,'authority':'setup-code-only'}
