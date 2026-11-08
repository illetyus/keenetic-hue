"""Run only on the trusted workstation after acceptance, before transfer."""
import argparse
import hashlib
import json
from pathlib import Path
from install import stage_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', type=Path)
    args = parser.parse_args()
    manifest = {name: hashlib.sha256((args.stage/name).read_bytes()).hexdigest()
                for name in sorted(stage_inputs(args.stage))}
    raw = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    (args.stage/'install-manifest.json').write_bytes(raw)
    print('Manifest SHA256:', hashlib.sha256(raw).hexdigest())
    print('Installer SHA256:', manifest['deploy/install.py'])


if __name__ == '__main__':
    main()
