"""Run only from a root-private, previously signature-verified installed slot."""
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode=True
root=Path('/opt/var/lib/keenetic-hue-system')
entry=Path(__file__).absolute()
if os.geteuid()!=0 or entry.is_symlink() or any(p.is_symlink() for p in entry.parents):
    raise SystemExit('ACCEPTANCE_ENTRY_OWNER')
try:parts=entry.relative_to(root/'releases').parts
except ValueError:raise SystemExit('ACCEPTANCE_ENTRY_PATH') from None
if (len(parts)!=3 or parts[1:]!=('service','acceptance-entry.py')
        or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',parts[0])):
    raise SystemExit('ACCEPTANCE_ENTRY_PATH')
slot=root/'releases'/parts[0]
for path in (root,root/'releases',slot,slot/'manager',slot/'manager/hue_admin'):
    info=path.stat()
    if not path.is_dir() or info.st_uid!=0 or info.st_mode&0o022:
        raise SystemExit('ACCEPTANCE_ENTRY_OWNER')
sys.path.insert(0,str(slot/'manager'))
from hue_admin.acceptance_entry import main
raise SystemExit(main(entry_path=entry))
