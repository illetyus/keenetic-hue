"""Apply a verified application snapshot with the existing service stopped."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re


def _write_private(path,data,mode=0o600):
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0)
    fd=os.open(path,flags,mode)
    try:
        if hasattr(os,'fchmod'):
            try:os.fchmod(fd,mode)
            except OSError:pass
        view=memoryview(data)
        while view:
            written=os.write(fd,view)
            view=view[written:]
    finally:
        os.close(fd)


def _replace_private(path,data,mode=0o600):
    flags=os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0)
    fd=os.open(path,flags,mode)
    try:
        if hasattr(os,'fchmod'):
            try:os.fchmod(fd,mode)
            except OSError:pass
        view=memoryview(data)
        while view:
            written=os.write(fd,view)
            view=view[written:]
    finally:
        os.close(fd)


def load_verified_snapshot(installer, installer_digest, stage, manifest_digest):
    installer=Path(installer)
    if installer.is_symlink() or not re.fullmatch('[0-9a-f]{64}',installer_digest):
        raise ValueError('Invalid installer digest or path')
    source=installer.read_bytes()
    if not hmac.compare_digest(hashlib.sha256(source).hexdigest(),installer_digest):
        raise ValueError('Trusted installer digest mismatch')
    scope={'__name__':'verified_installer','__file__':str(installer)}
    exec(compile(source,str(installer),'exec'),scope)
    return scope['load_snapshot'](Path(stage),manifest_digest)


load_trusted_snapshot=load_verified_snapshot


def apply_snapshot(snapshot,target,config,tag):
    target=Path(target)
    if not re.fullmatch('[a-z0-9-]{1,48}',tag):raise ValueError('Invalid update tag')
    if any(p.is_symlink() for p in (target,*target.parents)):
        raise ValueError('Symlink target')
    root=target.resolve(strict=True)
    app=root/'app'
    cfg=root/'config.json'
    for p in (app,cfg,root/'backups'):
        if p.is_symlink():raise ValueError('Symlink install path')
    if not app.is_dir() or not cfg.is_file():raise ValueError('Existing install required')
    backup=root/'backups'/tag
    candidate=root/('app.new-'+tag)
    temporary=root/('config.new-'+tag)
    # Every rename destination is an explicit descendant of the verified install root.
    for p in (backup,candidate,temporary):
        if not p.resolve().is_relative_to(root):raise ValueError('Update path escapes target')
        if p.exists() or p.is_symlink():raise FileExistsError(str(p))
    backup.mkdir(mode=0o700,parents=True)
    candidate.mkdir(mode=0o700)
    for name,data in snapshot.items():
        if not name.startswith('hue_agent/'):continue
        rel=Path(name)
        if '..' in rel.parts or rel.is_absolute() or rel.suffix!='.py':raise ValueError('Unsafe application path')
        path=candidate/rel
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        _write_private(path,data)
    _write_private(candidate/'launch.py',snapshot['deploy/launch.py'])
    old_config=cfg.read_bytes()
    _write_private(backup/'config.json',old_config)
    _write_private(temporary,json.dumps(config,indent=2).encode())
    moved=False
    try:
        app.rename(backup/'app')
        moved=True
        candidate.rename(app)
        temporary.replace(cfg)
    except BaseException:
        if moved:
            if app.exists():app.rename(backup/'failed-app')
            (backup/'app').rename(app)
            _replace_private(cfg,old_config)
        raise
    return backup


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',type=Path,required=True)
    parser.add_argument('--trusted-installer',dest='installer_path',type=Path,required=True)
    parser.add_argument('--installer-sha256',required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--target',type=Path,default=Path('/opt/var/lib/hue-agent'))
    parser.add_argument('--tag',required=True)
    args=parser.parse_args()
    os.umask(0o077)
    if args.target.resolve()!=Path('/opt/var/lib/hue-agent'):
        raise SystemExit('Unexpected production update target')
    snapshot=load_verified_snapshot(args.installer_path,args.installer_sha256,args.stage,args.manifest_sha256)
    cfg_path=args.target/'config.json'
    if cfg_path.is_symlink() or cfg_path.stat().st_mode&0o077:
        raise SystemExit('Private current config required')
    config=json.loads(cfg_path.read_text())
    requested=json.loads(snapshot['config.json'])
    if set(requested)!={'cloud'} or not isinstance(requested['cloud'],dict):
        raise SystemExit('Update config may only set cloud settings')
    cloud=requested['cloud']
    if cloud.get('enabled') is not True or cloud.get('token_file')!=str(args.target/'cloud.token'):
        raise SystemExit('Unexpected cloud credential path')
    config['cloud']=cloud
    import fcntl
    with (args.target/'update.lock').open('a+') as deployment:
        fcntl.flock(deployment,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with open(config['lock_file'],'a+') as owner:
            fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
            apply_snapshot(snapshot,args.target,config,args.tag)
    print('Verified app updated; bond, local API token and startup script preserved.')


if __name__=='__main__':main()
