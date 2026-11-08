"""Explicit source allowlist and consistent online SQLite backup."""
import json
from contextlib import closing
import os
import sqlite3
import stat
import sys
import tempfile
import time
from pathlib import Path

from .manifest import MAX_BYTES, validate_name

LEGACY_AGENT = Path('/opt/var/lib/hue-agent')
SYSTEM = Path('/opt/var/lib/keenetic-hue-system')
LAYOUT = {
    'device_config':'/opt/var/lib/keenetic-hue/device.json',
    'keystore':'/opt/var/lib/keenetic-hue/keystore.json',
    'journal':'/opt/var/lib/hue-agent/commands.sqlite',
    'token_file':'/opt/var/lib/hue-agent/api.token',
}
SOURCES = {
    'config/agent.json':LEGACY_AGENT/'config.json',
    'config/device.json':Path(LAYOUT['device_config']),
    'config/management.json':SYSTEM/'config/management.json',
    'secrets/bond.json':Path(LAYOUT['keystore']),
    'secrets/api.token':Path(LAYOUT['token_file']),
    'secrets/device.token':LEGACY_AGENT/'cloud.token',
    'secrets/tunnel.token':Path('/opt/var/lib/hue-tunnel/tunnel.token'),
    'secrets/setup-issuer.token':SYSTEM/'secrets/setup-issuer.token',
}


def layout_for(root=None,unified=None):
    root=SYSTEM if root is None else Path(root)
    if unified is None:unified=(root/'active.json').exists() or (root/'state/schema.json').exists()
    if not unified:return dict(LAYOUT),dict(SOURCES)
    layout={'device_config':str(root/'config/device.json'),'keystore':str(root/'secrets/bond.json'),
            'journal':str(root/'state/commands.sqlite'),'token_file':str(root/'secrets/api.token')}
    sources={
        'config/agent.json':root/'config/agent.json','config/device.json':root/'config/device.json',
        'config/management.json':root/'config/management.json','config/release-trust.pub':root/'config/release-trust.pub',
        'secrets/bond.json':root/'secrets/bond.json','secrets/api.token':root/'secrets/api.token',
        'secrets/device.token':root/'secrets/device.token','secrets/tunnel.token':root/'secrets/tunnel.token',
        'secrets/setup-issuer.token':root/'secrets/setup-issuer.token'}
    return layout,sources


def validate_layout(config,root=None):
    root=SYSTEM if root is None else Path(root)
    if (root/'active.json').exists() or (root/'state/schema.json').exists():
        from .service_manager import validate_agent_config
        return validate_agent_config(root,config)
    if any(config.get(key) != value for key,value in LAYOUT.items()):
        raise ValueError('BACKUP_LAYOUT_UNSUPPORTED')
    tunnel=config.get('tunnel')
    if not isinstance(tunnel,dict) or tunnel.get('token_file') != str(SOURCES['secrets/device.token']):
        raise ValueError('BACKUP_LAYOUT_UNSUPPORTED')
    if config.get('cloud',{}).get('enabled'):
        raise ValueError('BACKUP_LEGACY_CLOUD_UNSUPPORTED')


def read_private_bytes(path, limit=MAX_BYTES//2):
    path=Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('BACKUP_SOURCE_SYMLINK')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_BINARY',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size>limit:
            raise ValueError('BACKUP_SOURCE_SIZE')
        if sys.platform=='linux' and info.st_mode & 0o077:
            raise ValueError('BACKUP_SOURCE_PERMISSIONS')
        with os.fdopen(os.dup(fd),'rb') as stream:
            data=stream.read(limit+1)
        if len(data)>limit:
            raise ValueError('BACKUP_SOURCE_SIZE')
        return data
    finally:
        os.close(fd)


def sqlite_snapshot(path):
    path=Path(path).absolute()
    # Check file type, bounds and permissions without reading the active DB as a backup.
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError('BACKUP_SOURCE_SYMLINK')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size>MAX_BYTES//2:
        raise ValueError('BACKUP_DATABASE_SIZE')
    if sys.platform=='linux' and info.st_mode & 0o077:
        raise ValueError('BACKUP_SOURCE_PERMISSIONS')
    deadline=time.monotonic()+10
    page_size=4096
    def progress(status,remaining,total):
        if time.monotonic()>deadline or total*page_size>MAX_BYTES//2:
            raise ValueError('BACKUP_DATABASE_BUDGET')
    with tempfile.TemporaryDirectory(prefix='hue-db-snapshot-') as tmp:
        target=Path(tmp)/'snapshot.sqlite'
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as source:
            page_size=source.execute('pragma page_size').fetchone()[0]
            with closing(sqlite3.connect(target)) as destination:
                source.backup(destination,pages=64,progress=progress,sleep=0.01)
                if destination.execute('pragma quick_check').fetchone()!=('ok',):
                    raise ValueError('BACKUP_DATABASE_INTEGRITY')
        return read_private_bytes(target)


def capture(sources, journal):
    files={validate_name(name):read_private_bytes(path) for name,path in sources.items()}
    if journal is not None:
        files['state/commands.sqlite']=sqlite_snapshot(journal)
    # Immutable config/credentials must not change during the online database snapshot.
    for name,path in sources.items():
        if files[name]!=read_private_bytes(path):
            raise ValueError('BACKUP_SOURCE_CHANGED')
    if sum(map(len,files.values()))>MAX_BYTES//2:
        raise ValueError('BACKUP_TOO_LARGE')
    return files


def capture_restricted(root,release_id,sources,journal):
    from .agent_runtime import _marker
    from .agent_storage import agent_runtime_paths,read_agent_private
    from .agent_snapshot import snapshot_agent_database,validate_agent_database_bytes
    from .privileges import load_service_identity
    identity=load_service_identity(root,'hue-agent');uid,gid=identity['uid'],identity['gid']
    marker=_marker(root,identity);paths=agent_runtime_paths(root,release_id)
    runtime_authoritative=marker is not None and marker['phase']=='ready'
    def read_source(name,path):
        if runtime_authoritative and name=='secrets/bond.json':
            return read_agent_private(paths['keystore'],uid,gid)
        return read_private_bytes(path)
    files={validate_name(name):read_source(name,path) for name,path in sources.items()}
    if runtime_authoritative:
        database=snapshot_agent_database(paths['journal'],uid,gid,output_dir=root/'run')
    else:
        database=validate_agent_database_bytes(root,release_id,read_private_bytes(journal))
    files['state/commands.sqlite']=database
    for name,path in sources.items():
        if files[name]!=read_source(name,path):raise ValueError('BACKUP_SOURCE_CHANGED')
    if sum(map(len,files.values()))>MAX_BYTES//2:raise ValueError('BACKUP_TOO_LARGE')
    return files


def capture_current(root=None):
    root=SYSTEM if root is None else Path(root)
    layout,sources=layout_for(root)
    before=read_private_bytes(sources['config/agent.json'],8192)
    config=json.loads(before)
    validate_layout(config,root)
    restricted_release=None
    if (root/'active.json').exists() or (root/'active.json').is_symlink():
        from .service_manager import selection
        from .runtime_policy import release_policy
        selected=selection(root)
        if release_policy(root,selected)['agent']=='dedicated':restricted_release=selected
    files=(capture_restricted(root,restricted_release,sources,Path(layout['journal'])) if restricted_release
           else capture(sources,Path(layout['journal'])))
    if files['config/agent.json']!=before:
        raise ValueError('BACKUP_SOURCE_CHANGED')
    identity={'adapter':config['controller_public_address'],'lamp':config['address']}
    return files,identity
