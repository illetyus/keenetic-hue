"""Detach only the reviewed Hue agent; the owner publishes its own PID."""
import json
import os
import subprocess
import sys
from pathlib import Path

config_path = Path(sys.argv[1]).resolve()
base = config_path.parent
config = json.loads(config_path.read_text())
environment = dict(os.environ)
environment['PYTHONPATH'] = config.get('app_directory', str(base))
os.umask(0o077)
with (base/'bootstrap.log').open('ab') as output:
    subprocess.Popen([sys.executable,'-u','-m','hue_agent','--config',str(config_path)],
                     cwd=base,env=environment,stdin=subprocess.DEVNULL,
                     stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
