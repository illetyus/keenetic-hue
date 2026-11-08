"""Stable bootstrap verifies the selected manager before importing its code."""
import hashlib
import importlib
import json
from pathlib import Path
import sys

from .platform import runtime_for
from .releases import verify_signed_manifest
from .service_manager import private_bytes,selection


def selected_manager(root,runtime_probe=runtime_for):
    root=Path(root).absolute();release_id=selection(root)
    release=root/'releases'/release_id
    raw=private_bytes(release/'verified-manifest.json',128*1024)
    key=private_bytes(root/'config/release-trust.pub',32)
    value=json.loads(raw)
    manifest=verify_signed_manifest(raw,key,value.get('architecture'),value.get('pythonAbi'),value.get('dependencies'))
    runtime=runtime_probe(manifest['dependencies'])
    verify_signed_manifest(raw,key,*runtime)
    if manifest['releaseId']!=release_id:raise ValueError('BOOTSTRAP_RELEASE_ID')
    files={name:entry for name,entry in manifest['files'].items() if name.startswith('manager/')}
    if not {'manager/hue_admin/__init__.py','manager/hue_admin/cli.py'}.issubset(files):
        raise ValueError('BOOTSTRAP_MANAGER_MISSING')
    actual=set()
    for path in (release/'manager').rglob('*'):
        if path.is_symlink():raise ValueError('BOOTSTRAP_MANAGER_SYMLINK')
        if path.is_file():actual.add(path.relative_to(release).as_posix())
    if actual!=set(files):raise ValueError('BOOTSTRAP_MANAGER_UNDECLARED_FILE')
    for name,entry in files.items():
        raw=private_bytes(release/name,1024*1024)
        if len(raw)!=entry['size'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            raise ValueError('BOOTSTRAP_MANAGER_HASH')
    return release/'manager'


def run(root,argv):
    root=Path(root).absolute()
    # huectl-entry holds CLI admission here. Recover before choosing a manager:
    # recovery may restore the baseline selection after an interrupted test.
    if (argv and argv[0] in ('start','restart','recover','install','repair','update','rollback')
            or argv[:2] in (['internal-service','start'],['internal-service','restart'])):
        from .acceptance_startup import recover_before_start
        recover_before_start(root)
    # The authenticated installer stages the transition manager before update.
    # Do not retain the old selected manager's process contract across activation.
    recovery=bool(argv and argv[0] in ('recover','install','repair','internal-service','update'))
    if recovery:
        manager=root/'manager'
    elif (root/'active.json').exists() or (root/'active.json').is_symlink():
        manager=selected_manager(root)
    elif (root/'state/schema.json').exists():
        raise ValueError('BOOTSTRAP_SELECTION_MISSING')
    else:
        manager=root/'manager'
    sys.path.insert(0,str(manager))
    return importlib.import_module('hue_admin.cli').main(argv)
