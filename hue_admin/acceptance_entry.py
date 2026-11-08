"""Fixed signed-slot acceptance entry; never provisions trust or storage proof."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import uuid

from . import service_manager as manager
from .acceptance_journal import AcceptanceJournal
from .acceptance_launcher import launch_candidate
from .acceptance_startup import physical_snapshot,recover_before_start
from .admission import admit
from .controlled_acceptance import ManagedRelease
from .storage_acceptance import require_current


def entry_candidate(entry,root):
    try:parts=Path(entry).absolute().relative_to(Path(root).absolute()/'releases').parts
    except ValueError:raise ValueError('ACCEPTANCE_ENTRY_PATH') from None
    if (len(parts)!=3 or not manager.valid_id(parts[0])
            or parts[1:]!=('service','acceptance-entry.py')):
        raise ValueError('ACCEPTANCE_ENTRY_PATH')
    return parts[0]


def verified_candidate(root,candidate):
    from .activation import verify_slot
    from .bootstrap_install import manager_matches
    from .release_commands import trusted_key,runtime_from_manifest
    from .runtime_policy import release_policy
    root=Path(root)
    key=trusted_key(root)
    raw=manager.private_bytes(root/'releases'/candidate/'verified-manifest.json',128*1024)
    runtime=runtime_from_manifest(raw,key)
    slot=verify_slot(root,candidate,key,runtime)
    policy=release_policy(root,candidate)
    if policy!={'schema':1,'agent':'dedicated','tunnel':'dedicated','childIdentity':True,'kernel':False}:
        raise ValueError('ACCEPTANCE_RUNTIME_POLICY')
    manifest=json.loads(raw)
    files={name[len('manager/'):]:manager.private_bytes(slot/name,1024*1024)
           for name in manifest['files'] if name.startswith('manager/')}
    if not manager_matches(root/'manager',files):
        raise ValueError('ACCEPTANCE_BOOT_RECOVERY_NOT_INSTALLED')


def launch_from_slot(candidate,archive,pin,*,root=manager.SYSTEM):
    root=Path(root).absolute()
    if (not manager.valid_id(candidate) or type(pin) is not str or not re.fullmatch('[a-f0-9]{64}',pin)):
        raise ValueError('ACCEPTANCE_ENTRY_INPUT')
    with admit(root,['acceptance-test']):
        recover_before_start(root)
        verified_candidate(root,candidate)
        baseline=manager.selection(root)
        if baseline==candidate:raise ValueError('ACCEPTANCE_BASELINE_EQUALS_CANDIDATE')
        adapter=ManagedRelease(root,archive,pin,observe=lambda:physical_snapshot(root),
                               storage_ready=lambda:require_current(root))
        # Preflight before returning a started reply, and again inside the child lock.
        adapter.preflight(candidate)
        operation=str(uuid.uuid4())
        pid=launch_candidate(adapter,AcceptanceJournal(root),baseline,candidate,operation)
    return {'status':'started','operationId':operation,'guardianPid':pid,
            'baselineRelease':baseline,'candidateRelease':candidate}


def main(argv=None,*,entry_path):
    parser=argparse.ArgumentParser(description='Signed Keenetic Hue acceptance entry')
    sub=parser.add_subparsers(dest='action',required=True)
    run=sub.add_parser('run');run.add_argument('archive',type=Path)
    run.add_argument('--sha256',required=True)
    sub.add_parser('status')
    args=parser.parse_args(argv)
    try:
        if os.geteuid()!=0:raise ValueError('ROOT_REQUIRED')
        candidate=entry_candidate(entry_path,manager.SYSTEM)
        if args.action=='run':
            value=launch_from_slot(candidate,args.archive,args.sha256)
        else:
            verified_candidate(manager.SYSTEM,candidate)
            state=AcceptanceJournal(manager.SYSTEM).load()
            value={key:state[key] for key in ('operationId','phase','outcome','restoreVerified',
                                              'baselineRelease','candidateRelease')}
        print(json.dumps(value));return 0
    except Exception:
        print('ACCEPTANCE_ENTRY_FAILED: check signed slot, boot recovery, storage evidence and journal',file=sys.stderr)
        return 1
