"""Encrypted restore with real restricted fake agent; no USB/Tunnel/account I/O."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin import service_manager as manager
from hue_admin.agent_storage import agent_runtime_paths
from hue_admin.backup import seal
from hue_admin.installation import install_candidate
from hue_admin.releases import build_release,verify_release
from hue_admin.snapshot import capture_current
from hue_admin.restore_current import restore_from

FAKE_AGENT=r'''
import hashlib,json,os,signal,sys,time
from pathlib import Path
cfg=json.loads(Path(sys.argv[-1]).read_text());pid=Path(cfg['pid_file'])
pid.write_text(str(os.getpid()))
ready=pid.parent/'test-ready.json'
ready.write_text(json.dumps({'pid':os.getpid(),'uid':os.getuid(),'gid':os.getgid(),
 'tokenDigest':hashlib.sha256(Path(cfg['token_file']).read_bytes()).hexdigest()}))
signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
try:
 while True:time.sleep(.05)
finally:
 if pid.exists() and pid.read_text()==str(os.getpid()):pid.unlink()
'''


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'Native restricted process restore')
class RestrictedRestoreTests(unittest.TestCase):
    release='0.2.0-rc.6'

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hue-restricted-restore-')
        self.base=Path(self.temp.name);self.base.chmod(0o755);self.root=self.base/'system'
        self.addCleanup(self.cleanup_fixture)
        used=set()
        for path in map(Path,('/etc/passwd','/etc/group','/opt/etc/passwd','/opt/etc/group')):
            for line in path.read_text().splitlines():
                fields=line.split(':')
                if len(fields) in (4,7) and fields[2].isdigit():used.add(int(fields[2]))
                if len(fields)==7 and fields[3].isdigit():used.add(int(fields[3]))
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():continue
            try:
                rows=dict(line.split(':',1) for line in (proc/'status').read_text().splitlines() if ':' in line)
                used.update(map(int,rows.get('Uid','').split()));used.update(map(int,rows.get('Gid','').split()))
            except (OSError,ValueError):pass
        numbers=[number for number in range(62000,63000) if number not in used][:2]
        if len(numbers)!=2:raise RuntimeError('FIXTURE_IDENTITIES_UNAVAILABLE')
        self.tunnel_uid,self.uid=numbers
        self.host_used=used
        tables={}
        for name in ('native_passwd','native_group','entware_passwd','entware_group'):
            path=self.base/name;path.write_text('root:*:0:0::/root:/bin/sh\n' if name.endswith('passwd') else 'root:*:0:\n')
            path.chmod(0o600);tables[name]=path
        source=Path(__file__).resolve().parents[1]
        files={'manager/hue_admin/'+path.name:path.read_bytes() for path in (source/'hue_admin').glob('*.py')}
        # Signed test-only adapters redirect account files and never touch USB.
        account=files['manager/hue_admin/account_store.py'].decode()
        account+='\nSYSTEM=Path('+repr(str(self.root))+')\nACCOUNT_PATHS={'
        account+=','.join(repr(name)+':Path('+repr(str(path))+')' for name,path in tables.items())+'}\n'
        files['manager/hue_admin/account_store.py']=account.encode()
        files['manager/hue_admin/usb_permissions.py']=b'''from pathlib import Path
from .restore_transaction import atomic_replace
def prepare_usb_permissions(root,*a,**kw):
 path=Path(root)/'config/usb.json'
 if not path.exists():atomic_replace(path,b'{"schema":1,"vendor":"050d","product":"065a","serial":"AABBCCDDEEFF"}')
 return {"fixture":True}
def restore_usb_permissions(*a,**kw):return {"fixture":True}
'''
        for name in ('component-child.py','hue-supervise.sh','huectl','S97keenetic-hue'):files['service/'+name]=(source/'deploy'/name).read_bytes()
        files['manager/huectl-entry.py']=(source/'deploy/huectl-entry.py').read_bytes()
        files['metadata/state-schema.json']=b'{"schema":1,"read":[1],"write":1}'
        files['bin/cloudflared']=b'\x7fELFfixture-only-not-executable'
        files.update({'agent/hue_agent/__init__.py':b'', 'agent/hue_agent/__main__.py':FAKE_AGENT.encode(),
                      'metadata/runtime-policy.json':b'{"schema":1,"agent":"dedicated","tunnel":"dedicated","childIdentity":true,"kernel":false}'})
        signing=Ed25519PrivateKey.generate();public=signing.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        archive=self.base/'release.huerel';runtime=('mips-3.4','cp313',{'bumble':'0.0.236'})
        self.release_archive,self.public,self.runtime,self.tables=archive,public,runtime,tables
        build_release(files,archive,signing,self.release,*runtime,release_sequence=6)
        install_candidate(verify_release(archive,public,*runtime),self.root,'fresh')
        profile={'hue-tunnel':{'schema':1,'uid':self.tunnel_uid,'gid':self.tunnel_uid},
                 'hue-agent':{'schema':1,'uid':self.uid,'gid':self.uid}}
        def write(name,value):
            path=self.root/name;path.write_bytes(value if isinstance(value,bytes) else json.dumps(value).encode());path.chmod(0o600)
        write('state/service-identities.json',profile)
        write('active.json',{'schema':1,'releaseId':self.release})
        write('state/schema.json',{'schema':1})
        write('config/usb.json',{'schema':1,'vendor':'050d','product':'065a','serial':'AABBCCDDEEFF'})
        write('config/device.json',{'name':'fixture'})
        write('config/management.json',{'publicOrigin':'https://fixture.test'})
        write('secrets/bond.json',{'fixture':{'key':'fake'}})
        for name in ('api','device','tunnel','setup-issuer'):write('secrets/'+name+'.token',b'F'*64)
        config=manager.agent_paths(self.root)
        config.update(transport='usb:fixture',address='fixture-lamp',controller_public_address='fixture-adapter',
                      port=8787,cloud={'enabled':False},tunnel={'enabled':True,'port':8788,'token_file':str(self.root/'secrets/device.token')})
        write('config/agent.json',config)
        with sqlite3.connect(self.root/'state/commands.sqlite') as db:
            db.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
            db.execute('CREATE TABLE sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)')
            db.execute("INSERT INTO commands VALUES ('original','hash','{}',1)")
        (self.root/'state/commands.sqlite').chmod(0o600)
        self.paths=agent_runtime_paths(self.root,self.release);self.fail_next_start=False
        self.assertTrue(self.service('agent','start'))

    def service(self,component,verb):
        if component=='tunnel':return True  # Explicit synthetic boundary; no network or actual Tunnel.
        if verb=='start' and self.fail_next_start:self.fail_next_start=False;return False
        result=manager.action(component,verb,self.root)
        if result and verb in ('start','restart'):
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                if self.healthy()['healthy']:return True
                time.sleep(.1)
            return False
        return result

    def healthy(self):
        try:
            pid=manager.child_pid('agent',self.root);record=manager.owner_record('agent',self.root)
            ready=json.loads((self.paths['run_dir']/'test-ready.json').read_bytes())
            digest=hashlib.sha256((self.root/'secrets/api.token').read_bytes()).hexdigest()
            good=bool(pid and ready=={'pid':pid,'uid':self.uid,'gid':self.uid,'tokenDigest':digest}
                      and manager.child_owned('agent',self.root,record,pid))
            return {'healthy':good,'releaseId':self.release if good else None}
        except (OSError,ValueError,KeyError):return {'healthy':False,'releaseId':None}

    def cleanup_fixture(self):
        if (self.root/'run').is_dir():self.assertTrue(manager.action('agent','stop',self.root))
        self.temp.cleanup()

    def backup_and_change_current(self):
        files,identity=capture_current(self.root)
        archive=self.base/'test.huebak';archive.write_bytes(seal(files,'fixture-password',identity));archive.chmod(0o600)
        with sqlite3.connect(self.paths['journal']) as db:db.execute("INSERT INTO commands VALUES ('later','hash','{}',2)")
        self.paths['keystore'].write_text('{"current":{"key":"fake"}}')
        (self.root/'secrets/api.token').write_bytes(b'G'*64)
        self.assertTrue(self.service('agent','restart'))
        return archive

    def rows(self):
        with sqlite3.connect(self.paths['journal']) as db:return db.execute('select id from commands order by created').fetchall()

    def test_encrypted_restore_restarts_actual_dedicated_agent_with_backup_state(self):
        archive=self.backup_and_change_current()
        with patch('hue_admin.restore_current.SYSTEM',self.root), \
             patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
            result=restore_from(archive,'fixture-password',False,self.service,self.healthy)
        self.assertTrue(result['restored']);self.assertTrue(self.healthy()['healthy'])
        self.assertEqual(self.rows(),[('original',)])
        self.assertIn('fixture',json.loads(self.paths['keystore'].read_bytes()))
        self.assertEqual((self.root/'secrets/api.token').read_bytes(),b'F'*64)

    def test_failed_start_restores_latest_pre_restore_state_and_dedicated_owner(self):
        archive=self.backup_and_change_current();self.fail_next_start=True
        with patch('hue_admin.restore_current.SYSTEM',self.root), \
             patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
            with self.assertRaises(RuntimeError):restore_from(archive,'fixture-password',False,self.service,self.healthy)
        self.assertTrue(self.healthy()['healthy']);self.assertEqual(self.rows(),[('original',),('later',)])
        self.assertIn('current',json.loads(self.paths['keystore'].read_bytes()))
        self.assertEqual((self.root/'secrets/api.token').read_bytes(),b'G'*64)
        self.assertEqual(json.loads((self.root/'state/restore-pending/receipt.json').read_bytes())['phase'],'rolled_back')

    def test_fresh_usb_restore_after_stale_native_accounts_are_regenerated(self):
        from hue_admin.account_store import _ensure_accounts
        from hue_admin.bootstrap_install import provision_bootstrap
        from hue_admin.initialize_restore import initialize
        from hue_admin.migration import LegacyLayout
        files,identity=capture_current(self.root)
        backup=self.base/'fresh.huebak';backup.write_bytes(seal(files,'fixture-password',identity));backup.chmod(0o600)
        old_uid=self.uid
        self.assertTrue(self.service('agent','stop'))
        self.root.rename(self.base/'previous-system')
        self.paths['base'].parent.rename(self.base/'previous-runtime')
        install_candidate(verify_release(self.release_archive,self.public,*self.runtime),self.root,'fresh')
        # Fresh Entware tables; native /tmp tables can still contain stale rows
        # until the firmware regenerates them at its next boot.
        reset={}
        for name,path in self.tables.items():
            value='root:*:0:0::/root:/bin/sh\n' if name.endswith('passwd') else 'root:*:0:\n'
            for number in sorted(self.host_used):
                if 61000<=number<62000:
                    value+=(f'reserved{number}:*:{number}:{number}::/nonexistent:/bin/false\n'
                            if name.endswith('passwd') else f'reserved{number}:*:{number}:\n')
            reset[name]=value
            if name.startswith('entware_'):path.write_text(value)
        before={name:path.read_bytes() for name,path in self.tables.items()}
        entries=self.base/'entries';entries.mkdir()
        cli,init=entries/'huectl',entries/'S97keenetic-hue'
        with patch('hue_admin.account_store.ensure_accounts',side_effect=lambda root:_ensure_accounts(root,self.tables)):
            with self.assertRaisesRegex(ValueError,'ACCOUNT_UNPROFILED_NAME'):
                provision_bootstrap(self.root,self.release,self.public,self.runtime,cli,init)
            self.assertEqual(before,{name:path.read_bytes() for name,path in self.tables.items()})
            self.assertFalse((self.root/'state/service-identities.json').exists())
            for name,path in self.tables.items():
                if name.startswith('native_'):path.write_text(reset[name])
            provision_bootstrap(self.root,self.release,self.public,self.runtime,cli,init)
        profile=json.loads((self.root/'state/service-identities.json').read_bytes())
        self.uid=profile['hue-agent']['uid'];self.assertNotEqual(self.uid,old_uid)
        self.assertNotIn(self.uid,self.host_used)
        legacy=LegacyLayout(*(self.base/name for name in ('absent-agent','absent-tunnel','absent-ble','absent-S99','absent-S98')))
        with patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
            result=initialize(self.root,backup,'fixture-password',self.release,self.public,self.runtime,
                              lambda verb:self.service('agent',verb),self.healthy,legacy=legacy)
        self.assertTrue(result['initialized']);self.assertTrue(self.healthy()['healthy'])
        self.assertEqual(self.rows(),[('original',)])
        self.assertEqual(self.paths['keystore'].stat().st_uid,self.uid)

    def test_real_role_uids_enforce_secret_and_code_permission_matrix(self):
        from hue_admin.privileges import prepare_tunnel_runtime,drop_identity
        tunnel_identity,tunnel=prepare_tunnel_runtime(self.root,self.release)
        ssh=self.root/'secrets/fixture-ssh-private';ssh.write_bytes(b'fake-ssh-key');ssh.chmod(0o600)
        code=r'''
import ctypes,json,os,sys
from pathlib import Path
value=json.loads(sys.argv[1])
assert os.getuid()==value['uid'] and os.getgid()==value['uid'] and os.getgroups()==[]
assert ctypes.CDLL(None).prctl(39,0,0,0,0)==1
rows=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert all(int(rows.get(key,'0').strip(),16)==0 for key in ('CapEff','CapPrm','CapAmb','CapInh'))
for path in value['readable']:
 with open(path,'rb') as source:assert source.read(1)
for path in value['denyRead']:
 try:
  with open(path,'rb') as source:source.read(1)
 except PermissionError:pass
 else:raise AssertionError('READ_SCOPE')
for path in value['denyWrite']:
 try:fd=os.open(path,os.O_WRONLY)
 except PermissionError:pass
 else:os.close(fd);raise AssertionError('WRITE_SCOPE')
Path(value['writable']).write_text('fixture-write')
print('ROLE_SCOPE_OK')
'''
        checks=(
            {'uid':tunnel_identity['uid'],'readable':[str(tunnel['token'])],
             'denyRead':[str(self.paths['token_file']),str(self.paths['keystore']),str(ssh)],
             'denyWrite':[str(tunnel['token']),str(tunnel['binary'])],
             'writable':str(tunnel['data']/'matrix.log')},
            {'uid':self.uid,'readable':[str(self.paths['token_file']),str(self.paths['keystore'])],
             'denyRead':[str(tunnel['token']),str(ssh)],
             'denyWrite':[str(self.paths['config']),str(self.paths['code']/'hue_agent/__main__.py'),
                          str(self.root/'config/release-trust.pub')],
             'writable':str(self.paths['log_dir']/'matrix.log')})
        for value in checks:
            def drop():drop_identity(value['uid'],value['uid'])
            result=subprocess.run([sys.executable,'-I','-c',code,json.dumps(value)],cwd='/',
                                  env={'PATH':'/opt/bin:/usr/bin:/bin','LANG':'C'},preexec_fn=drop,
                                  capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'ROLE_SCOPE_OK')

    def test_repeated_encrypted_snapshots_keep_worker_budget_and_current_rows(self):
        for iteration in range(8):
            try:
                files,identity=capture_current(self.root)
                archive=seal(files,'fixture-password',identity)
                self.assertTrue(archive)
            except Exception as error:
                rows=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
                error.add_note('fixtureIteration='+str(iteration)+' parentVmSize='+rows.get('VmSize','unavailable').strip())
                raise
        self.assertEqual(self.rows(),[('original',)])

    def test_signed_repair_rebuilds_bad_static_permissions_without_losing_current_state(self):
        from hue_admin.release_commands import execute
        self.backup_and_change_current()
        self.paths['config'].chmod(0o666)
        with patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
            result=execute('repair',self.release_archive,root=self.root,service=self.service,health=self.healthy)
        self.assertTrue(result['versionHealthVerified'])
        self.assertEqual(self.paths['config'].stat().st_mode&0o777,0o440)
        self.assertEqual(self.rows(),[('original',),('later',)])
        self.assertIn('current',json.loads(self.paths['keystore'].read_bytes()))
        self.assertTrue(self.healthy()['healthy'])

    def test_interrupted_runtime_repair_recovers_before_starting_agent(self):
        from hue_admin.runtime_repair import repair_runtime
        from hue_admin.release_commands import execute
        class PowerCut(BaseException):pass
        self.backup_and_change_current();self.assertTrue(self.service('agent','stop'))
        def cut(phase):
            if phase=='quarantined:4':raise PowerCut()
        with self.assertRaises(PowerCut):repair_runtime(self.root,self.release,failpoint=cut)
        self.assertEqual(json.loads((self.root/'state/runtime-repair.json').read_bytes())['phase'],'prepared')
        with patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
            result=execute('recover',root=self.root,service=self.service,health=self.healthy)
        self.assertTrue(result['runtimeRecovered']);self.assertTrue(self.healthy()['healthy'])
        self.assertEqual(self.rows(),[('original',),('later',)])

    def test_platform_verification_survives_boot_path_without_opt(self):
        with patch.dict(os.environ,{'PATH':'/usr/bin:/bin'}):
            self.assertEqual(manager.verify_selected(self.root,self.release),self.release)
