"""Exec the existing agent in foreground; its own USB lock remains authoritative."""
import json
import os
import sys
from pathlib import Path

base=Path('/opt/var/lib/hue-agent')
config=base/'config.json'
if config.is_symlink() or config.stat().st_mode & 0o077:
    raise SystemExit('PRIVATE_CONFIG_REQUIRED')
value=json.loads(config.read_text())
app=Path(value.get('app_directory',str(base/'app')))
if app != base/'app' or app.is_symlink():
    raise SystemExit('UNEXPECTED_APP_PATH')
os.chdir(base)
environment=dict(os.environ)
environment['PYTHONPATH']=str(app)
environment['HOME']=str(base)
os.execve(sys.executable,[sys.executable,'-u','-m','hue_agent','--config',str(config)],environment)
