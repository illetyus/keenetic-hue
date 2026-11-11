"""Exec one foreground component from the release pinned by its supervisor."""
import os
import sys
sys.dont_write_bytecode=True

component,root_value,release_id,owner_fd=sys.argv[1:]
if not owner_fd.isascii() or not owner_fd.isdigit() or not 3<=int(owner_fd)<=1048576:
    raise SystemExit('CHILD_OWNER_FD')
os.close(int(owner_fd))  # Close before importing configuration or executing the actual component.
import json
from pathlib import Path
import re

if component not in ('agent','tunnel') or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',release_id):
    raise SystemExit('CHILD_ARGUMENTS')
root=Path(root_value).absolute();release=root/'releases'/release_id
if root.is_symlink() or release.is_symlink() or any(p.is_symlink() for p in release.parents):raise SystemExit('CHILD_PATH')
sys.path.insert(0,str(release/'manager'))
from hue_admin.service_manager import private_bytes,validate_agent_config,environment
os.umask(0o077);os.chdir(root)
env=environment(root);env['HUE_RELEASE_ID']=release_id
from hue_admin.runtime_policy import release_policy
policy=release_policy(root,release_id)
if policy.get('childIdentity'):
    from hue_admin.child_identity import write_child_identity
    write_child_identity(root,component,release_id)
if component=='agent':
    if policy['agent']=='dedicated':
        from hue_admin.account_store import ensure_accounts
        from hue_admin.usb_permissions import prepare_usb_permissions
        from hue_admin.agent_runtime import prepare_agent_runtime,agent_environment
        from hue_admin.privileges import drop_identity
        ensure_accounts(root)
        prepare_usb_permissions(root,release_id)
        identity,paths=prepare_agent_runtime(root,release_id)
        env=agent_environment(paths,release_id)
        os.chdir(paths['state_dir'])
        drop_identity(identity['uid'],identity['gid'])
        os.execve(sys.executable,[sys.executable,'-s','-P','-u','-m','hue_agent','--config',str(paths['config'])],env)
        raise SystemExit('CHILD_EXEC_RETURNED')
    config=root/'config/agent.json'
    validate_agent_config(root,json.loads(private_bytes(config)))
    env['PYTHONPATH']=str(release/'agent')
    os.execve(sys.executable,[sys.executable,'-u','-m','hue_agent','--config',str(config)],env)
else:
    from hue_admin.account_store import ensure_accounts
    from hue_admin.privileges import prepare_tunnel_runtime,drop_identity,tunnel_environment,tunnel_arguments as restricted_arguments
    ensure_accounts(root)
    identity,paths=prepare_tunnel_runtime(root,release_id)
    ca=env.get('SSL_CERT_FILE')
    if not ca:raise SystemExit('CHILD_CA_MISSING')
    env=tunnel_environment(paths['data'],ca)
    os.chdir(paths['data'])
    drop_identity(identity['uid'],identity['gid'])
    log_path=paths['log']
    if not log_path.is_symlink() and log_path.is_file() and log_path.stat().st_size>=131072:
        log_path.replace(log_path.with_name(log_path.name+'.previous'))
    os.execve(paths['binary'],[str(paths['binary']),*restricted_arguments(paths['token'],paths['log'])],env)
