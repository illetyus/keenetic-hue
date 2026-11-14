import argparse
import json
import subprocess
import sys
import os
import warnings
from getpass import getpass, GetPassWarning
from .setup_code import issue_code
from .health import status
from .paths import SERVICES


def is_unified():
    from pathlib import Path
    root=Path('/opt/var/lib/keenetic-hue-system')
    return (root/'active.json').exists() or (root/'active.json').is_symlink() or (root/'state/schema.json').exists() or (root/'state/initialize.json').exists()


def lifecycle_guard():
    from .restore_current import management_lock
    return management_lock()


def lifecycle_operations(action,target):
    targets=[target] if target else ['agent','tunnel']
    if action=='stop':targets.reverse()
    if action=='restart' and not target:
        stopped=[run_service(component,'stop') for component in reversed(targets)]
        if not all(stopped):return 1
        action='start'
    if action=='stop':return 0 if all([run_service(component,'stop') for component in targets]) else 1
    for index,component in enumerate(targets):
        if not run_service(component,action):
            if is_unified():
                from .lifecycle_events import emit
                from .service_manager import SYSTEM
                for blocked in targets[index+1:]:emit(SYSTEM,blocked,'dependency_blocked','dependency','AGENT_START_FAILED')
            return 1
    return 0


def service_failure_details(target=None):
    from .lifecycle_events import last_failure
    from .service_manager import SYSTEM
    for component in ([target] if target else ('agent','tunnel')):
        value=last_failure(SYSTEM,component)
        if value:print(f"SERVICE_ACTION_FAILED component={component} stage={value['stage']} reason={value['reason']}",file=sys.stderr)


def release_action(operation,archive,trust_file,trust_pin,boot_action):
    from .release_commands import execute
    return execute(operation,archive,trust_file,trust_pin,boot_action,service=run_service,health=lambda:status(True))


def archive_action(action, path, password, dry_run, recovery):
    try:
        return _archive_action(action,path,password,dry_run,recovery)
    except Exception:
        raise RuntimeError('ARCHIVE_OPERATION_FAILED') from None


def initialize_archive_action(path,password,release_id,dry_run):
    from .initialize_restore import initialize
    from .restore_current import management_lock,SYSTEM
    from .snapshot import read_private_bytes
    from .release_commands import runtime_from_manifest
    from .migration import new_action
    import time
    with management_lock(SYSTEM):
        from .service_manager import valid_id
        if not valid_id(release_id):raise ValueError('INITIALIZE_RELEASE_ID')
        key=read_private_bytes(SYSTEM/'config/release-trust.pub',32)
        raw=read_private_bytes(SYSTEM/'releases'/release_id/'verified-manifest.json',128*1024)
        runtime=runtime_from_manifest(raw,key)
        def healthy():
            deadline=time.monotonic()+90
            while time.monotonic()<deadline:
                value=status(True)
                if value.get('healthy') and value.get('releaseId')==release_id:return value
                time.sleep(1)
            return {'healthy':False}
        return initialize(SYSTEM,path,password,release_id,key,runtime,lambda verb:new_action(verb,SYSTEM),healthy,dry_run)


def _archive_action(action, path, password, dry_run, recovery):
    from .archive_commands import backup_to
    from .restore_current import management_lock, restore_from, recover_current
    if recovery:return recover_current(run_service)
    if action=='backup':
        with management_lock():return backup_to(path,password)
    return restore_from(path,password,dry_run,run_service,lambda:status(True))


def run_service(target, action):
    if target not in SERVICES or action not in ('start', 'stop', 'restart'):
        return False
    from pathlib import Path
    system=Path('/opt/var/lib/keenetic-hue-system')
    if is_unified():
        from .service_manager import action as owner_action
        return owner_action(target,action,system)
    path = SERVICES[target]
    if path.is_symlink() or not path.is_file():
        return False
    try:
        result = subprocess.run([str(path), action], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=50)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog='huectl', description='Keenetic Hue management')
    sub = parser.add_subparsers(dest='action', required=True)
    for operation in ('install','repair','update'):
        p=sub.add_parser(operation,help='Verify and manage a signed release archive')
        p.add_argument('file')
        if operation in ('install','repair'):
            p.add_argument('--trust-key');p.add_argument('--trust-key-sha256')
    sub.add_parser('rollback',help='Activate the previous verified release')
    sub.add_parser('recover',help='Recover interrupted local maintenance')
    sub.add_parser('usb-event',help=argparse.SUPPRESS)
    p=sub.add_parser('internal-service',help=argparse.SUPPRESS)
    p.add_argument('service_action',choices=('start','stop','restart','status'))
    p=sub.add_parser('configure-issuer',help='Provision or rotate the narrow setup-code key')
    p.add_argument('--origin',required=True,help='Exact public HTTPS origin without a path')
    p=sub.add_parser('backup',help='Create a password-encrypted private backup')
    p.add_argument('file')
    p=sub.add_parser('restore',help='Verify or restore a same-device private backup')
    p.add_argument('file',nargs='?')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--recover',action='store_true',help='Roll back an interrupted restore')
    p.add_argument('--initialize',action='store_true',help='Restore into an empty canonical installation')
    p.add_argument('--release-id',help='Verified installed software release for --initialize')
    sub.add_parser('setup-code',help='Show a new ten-minute code in this terminal only')
    for action in ('start', 'stop', 'restart'):
        p = sub.add_parser(action)
        p.add_argument('target', choices=('agent','tunnel'), nargs='?')
    for action in ('status','health','diagnose'):
        p = sub.add_parser(action)
        p.add_argument('--json', action='store_true')
        if action == 'health':
            p.add_argument('--live', action='store_true')
    args = parser.parse_args(argv)
    if args.action=='usb-event':
        try:
            from .usb_permissions import refresh_usb_permissions
            from .service_manager import SYSTEM
            print(json.dumps(refresh_usb_permissions(SYSTEM)));return 0
        except (OSError,ValueError,KeyError,TypeError):
            print('USB_REFRESH_FAILED',file=sys.stderr);return 1
    if args.action in ('install','repair','update','rollback','recover','internal-service'):
        try:
            result=release_action(args.action,getattr(args,'file',None),getattr(args,'trust_key',None),
                                  getattr(args,'trust_key_sha256',None),getattr(args,'service_action',None))
            print(json.dumps(result));return 0
        except KeyboardInterrupt:
            print('RELEASE_OPERATION_INTERRUPTED: use huectl recover',file=sys.stderr);return 130
        except Exception as error:
            from .lifecycle_events import safe_reason
            print('RELEASE_OPERATION_FAILED stage=release_workflow reason='+safe_reason(error),file=sys.stderr)
            if args.action=='internal-service':service_failure_details()
            return 1
    if args.action=='configure-issuer':
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            print('PRIVATE_TERMINAL_REQUIRED',file=sys.stderr);return 2
        try:
            from .configure_issuer import configure_issuer
            with warnings.catch_warnings():
                warnings.simplefilter('error',GetPassWarning)
                token=getpass('Kurulum kodu uretici anahtari: ')
            print(json.dumps(configure_issuer(args.origin,token)));return 0
        except Exception:
            print('ISSUER_CONFIGURATION_FAILED: check the key and exact HTTPS origin',file=sys.stderr);return 1
    if args.action in ('backup','restore'):
        recovery=getattr(args,'recover',False)
        if args.action=='restore' and ((recovery and (args.file or args.dry_run)) or (not recovery and not args.file)):
            parser.error('restore requires FILE, or --recover without FILE')
        if args.action=='restore' and (bool(args.initialize)!=bool(args.release_id) or recovery and (args.initialize or args.release_id)):
            parser.error('--initialize requires --release-id; it cannot be combined with --recover')
        if not sys.stdin.isatty() or not sys.stderr.isatty():
            print('PRIVATE_TERMINAL_REQUIRED',file=sys.stderr);return 2
        try:
            password=None
            if not recovery:
                with warnings.catch_warnings():
                    warnings.simplefilter('error',GetPassWarning)
                    password=getpass('Yedek parolasi: ')
                    if not password:raise ValueError('EMPTY_PASSWORD')
                    if args.action=='backup' and password!=getpass('Parolayi tekrar girin: '):
                        raise ValueError('PASSWORD_MISMATCH')
            if getattr(args,'initialize',False):
                result=initialize_archive_action(args.file,password,args.release_id,args.dry_run)
            else:result=archive_action(args.action,args.file,password,getattr(args,'dry_run',False),recovery)
            print(json.dumps(result));return 0
        except (OSError,ValueError,RuntimeError,GetPassWarning,EOFError,KeyboardInterrupt):
            print('BACKUP_OR_RESTORE_FAILED: no secret details logged; check private paths, password and recovery status',file=sys.stderr)
            return 1
    if args.action=='setup-code':
        if not sys.stdout.isatty() or not sys.stdin.isatty():
            print('SETUP_CODE_REQUIRES_TERMINAL',file=sys.stderr)
            return 2
        try:
            value=issue_code()
        except RuntimeError:
            print('SETUP_CODE_UNAVAILABLE: check issuer provisioning and HTTPS connectivity',file=sys.stderr)
            return 1
        print('Kurulum kodu: '+value['code'])
        print('Gecerlilik: '+str(value['expiresIn'])+' saniye; tek kullanim.')
        return 0
    if args.action in ('start','stop','restart'):
        try:
            with lifecycle_guard():
                if args.action in ('start','restart') and is_unified():
                    from .release_commands import recover_locked
                    from .snapshot import SYSTEM
                    recover_locked(SYSTEM,run_service)
                result=lifecycle_operations(args.action,args.target)
                if result:service_failure_details(args.target)
                return result
        except (OSError,ValueError,RuntimeError) as error:
            from .lifecycle_events import safe_reason
            print('SERVICE_ACTION_FAILED stage=lifecycle_recovery reason='+safe_reason(error),file=sys.stderr);return 1
    value = status(args.action == 'diagnose' or getattr(args,'live',False))
    if args.json:
        print(json.dumps(value, ensure_ascii=False))
    else:
        for name, component in value['components'].items():
            print(f"{name}: {component['state']} ({component['reason']})")
    return 0 if value['healthy'] else 1
