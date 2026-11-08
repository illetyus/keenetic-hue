"""Execute a separately trusted copy with -I and a trusted manifest digest."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path

FIXED_INPUTS = {'deploy/install.py', 'deploy/launch.py', 'deploy/S99hue-agent',
                'config.json', 'api.token', 'acceptance-passed.json'}


def stage_inputs(stage):
    inputs = set(FIXED_INPUTS)
    app = stage/'hue_agent'
    if not app.is_dir() or app.is_symlink() or stage.is_symlink():
        raise ValueError('Invalid stage directory')
    for path in app.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlink in stage')
        if '__pycache__' in path.parts:
            continue
        if path.is_file():
            if path.suffix != '.py':
                raise ValueError('Unexpected application file')
            inputs.add(path.relative_to(stage).as_posix())
    if 'hue_agent/__init__.py' not in inputs:
        raise ValueError('Missing application package')
    return inputs


def load_snapshot(stage, expected_digest):
    if len(expected_digest) != 64 or any(c not in '0123456789abcdef' for c in expected_digest):
        raise ValueError('A trusted SHA256 manifest digest is required')
    manifest_path = stage/'install-manifest.json'
    if manifest_path.is_symlink():
        raise ValueError('Symlink manifest')
    raw = manifest_path.read_bytes()
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_digest):
        raise ValueError('Manifest digest mismatch')
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or set(manifest) != stage_inputs(stage):
        raise ValueError('Manifest inventory mismatch')
    snapshot = {}
    for name, digest in manifest.items():
        path = stage/name
        if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != stage.parent):
            raise ValueError('Symlink input')
        data = path.read_bytes()
        if not isinstance(digest, str) or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest):
            raise ValueError('Input digest mismatch: '+name)
        snapshot[name] = data
    return snapshot


def install_snapshot(snapshot, target, startup):
    acceptance = json.loads(snapshot['acceptance-passed.json'])
    if not acceptance.get('restored') or acceptance['controls'] != 50 or acceptance['queries'] != 50 or acceptance['reconnections'] != 5:
        raise SystemExit('Foreground acceptance evidence missing')
    if acceptance['max_rss_kib'] > 48*1024 or acceptance['min_available_kib'] < 62580 or acceptance['dns_failures']:
        raise SystemExit('Resource or DNS acceptance failed')
    if target.exists() or target.is_symlink() or startup.exists() or startup.is_symlink():
        raise SystemExit('Existing agent install found; refusing overwrite')
    config = json.loads(snapshot['config.json'])
    app = target/'app'
    config['app_directory'] = str(app)
    for key, filename in (('token_file','api.token'),('journal','commands.sqlite'),('pid_file','agent.pid'),('log_file','agent.log')):
        config[key] = str(target/filename)
    target.mkdir(mode=0o700)
    app.mkdir(mode=0o700)
    for name, data in snapshot.items():
        if name.startswith('hue_agent/'):
            destination = app/name
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(data)
    (app/'launch.py').write_bytes(snapshot['deploy/launch.py'])
    (target/'api.token').write_bytes(snapshot['api.token'])
    (target/'config.json').write_text(json.dumps(config, indent=2))
    with startup.open('xb') as init_file:
        init_file.write(snapshot['deploy/S99hue-agent'])
    startup.chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    # No staged Python code is imported or executed. Only verified bytes are used.
    snapshot = load_snapshot(args.stage, args.manifest_sha256)
    import fcntl
    os.umask(0o077)
    with open('/opt/var/lib/keenetic-hue/agent.lock', 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        install_snapshot(snapshot, Path('/opt/var/lib/hue-agent'), Path('/opt/etc/init.d/S99hue-agent'))
    print('Installed verified snapshot. Run S99hue-agent start to activate.')


if __name__ == '__main__':
    main()
