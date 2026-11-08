"""Read exact target versions; never upgrade system or Python packages implicitly."""
from importlib.metadata import version,PackageNotFoundError
import subprocess
import sys


def runtime_for(required):
    if not isinstance(required,dict):raise ValueError('PLATFORM_DEPENDENCIES')
    result=subprocess.run(['/opt/bin/opkg','print-architecture'],capture_output=True,text=True,timeout=5,check=True)
    if 'arch mips-3.4 150' not in result.stdout.splitlines():raise ValueError('PLATFORM_UNSUPPORTED_ARCHITECTURE')
    native={}
    if any(name.startswith('opkg.') for name in required):
        installed=subprocess.run(['/opt/bin/opkg','list-installed'],capture_output=True,text=True,timeout=5,check=True)
        for line in installed.stdout.splitlines():
            parts=line.split(' - ')
            if len(parts)==2:native[parts[0]]=parts[1]
    versions={}
    for name in required:
        try:versions[name]=native[name[5:]] if name.startswith('opkg.') else version(name)
        except (PackageNotFoundError,KeyError):raise ValueError('PLATFORM_DEPENDENCY_MISSING') from None
    return 'mips-3.4',f'cp{sys.version_info.major}{sys.version_info.minor}',versions
