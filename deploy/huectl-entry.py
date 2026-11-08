"""Installed in a root-private management directory, independently of service state."""
import sys
import importlib.util
from pathlib import Path

sys.dont_write_bytecode=True
manager=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_hue_bootstrap',manager/'hue_admin/__init__.py',submodule_search_locations=[str(manager/'hue_admin')])
package=importlib.util.module_from_spec(spec);sys.modules['_hue_bootstrap']=package;spec.loader.exec_module(package)
try:
    from _hue_bootstrap.admission import admit
    with admit(manager.parent,sys.argv[1:]) as proceed:
        if proceed:
            from _hue_bootstrap.bootstrap import run
            result=run(manager.parent,sys.argv[1:])
        else:
            print('SERVICE_ALREADY_MANAGED: connectivity requires health check')
            result=0
except Exception:
    print('HUE_BOOTSTRAP_VERIFICATION_FAILED: use the trusted recovery command',file=sys.stderr)
    result=1
raise SystemExit(result)
