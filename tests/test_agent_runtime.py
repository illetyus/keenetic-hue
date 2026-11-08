import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release,verify_release
from hue_admin.installation import install_candidate
from hue_admin.service_manager import agent_paths
from hue_admin.agent_runtime import prepare_agent_runtime,checkpoint_agent_state,agent_environment


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'Native agent runtime ownership')
class AgentRuntimeTests(unittest.TestCase):
    def test_fresh_restore_validation_uses_explicit_signed_release_and_uid_worker(self):
        from hue_admin.snapshot import capture_restricted,layout_for
        from hue_admin.initialize_restore import validate_files
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);layout,sources=layout_for(root,True)
            for path in sources.values():
                if not path.exists():path.write_bytes(b'F'*64);path.chmod(0o600)
            sources['config/management.json'].write_text('{"publicOrigin":"https://fixture.test"}')
            files=capture_restricted(root,'0.2.0-rc.6',sources,Path(layout['journal']))
            config=json.loads(files['config/agent.json'])
            identity={'adapter':config['controller_public_address'],'lamp':config['address']}
            self.assertFalse((root/'active.json').exists())
            with patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
                validate_files(root,files,identity,(root/'config/release-trust.pub').read_bytes(),release_id='0.2.0-rc.6')

    def test_restore_validation_never_parses_agent_database_as_root(self):
        from hue_admin.snapshot import capture_current,layout_for
        from hue_admin.restore_current import validate_files
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);_,sources=layout_for(root,True)
            for path in sources.values():
                if not path.exists():path.write_bytes(b'F'*64);path.chmod(0o600)
            sources['config/management.json'].write_text('{"publicOrigin":"https://fixture.test"}')
            prepare_agent_runtime(root,'0.2.0-rc.6')
            active=root/'active.json';active.write_text('{"schema":1,"releaseId":"0.2.0-rc.6"}');active.chmod(0o600)
            files,identity=capture_current(root)
            with patch('hue_admin.restore_current.SYSTEM',root), \
                 patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
                validate_files(files,identity)

    def test_restore_checkpoint_uses_completed_agent_export_without_root_sqlite(self):
        from hue_admin.restore_current import checkpoint
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);prepare_agent_runtime(root,'0.2.0-rc.6')
            active=root/'active.json';active.write_text('{"schema":1,"releaseId":"0.2.0-rc.6"}');active.chmod(0o600)
            checkpoint_agent_state(root,'0.2.0-rc.6')
            with patch('hue_admin.restore_current.SYSTEM',root), \
                 patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
                checkpoint()

    def test_backup_reads_current_runtime_without_root_sqlite_parsing(self):
        from hue_admin.snapshot import capture_current,layout_for
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);_,sources=layout_for(root,True)
            for path in sources.values():
                if not path.exists():path.write_bytes(b'F'*64);path.chmod(0o600)
            _,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
            active=root/'active.json';active.write_text('{"schema":1,"releaseId":"0.2.0-rc.6"}');active.chmod(0o600)
            paths['keystore'].write_text('{"latest":{"key":"dummy"}}')
            with sqlite3.connect(paths['journal']) as db:
                db.execute("INSERT INTO commands VALUES ('current','hash','{}',1)")
            with patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
                files,identity=capture_current(root)
            self.assertIn('latest',json.loads(files['secrets/bond.json']))
            self.assertEqual(set(files),set(sources)|{'state/commands.sqlite'})
            copied=root/'state/backup-test.sqlite';copied.write_bytes(files['state/commands.sqlite'])
            with sqlite3.connect(copied) as db:self.assertEqual(db.execute('select id from commands').fetchall(),[('current',)])

    def test_restore_database_bytes_are_parsed_only_after_uid_drop(self):
        from hue_admin.agent_snapshot import validate_agent_database_bytes
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);raw=(root/'state/commands.sqlite').read_bytes()
            with patch('sqlite3.connect',side_effect=AssertionError('root parsed SQL')):
                checked=validate_agent_database_bytes(root,'0.2.0-rc.6',raw)
            self.assertTrue(checked.startswith(b'SQLite format 3\x00'))
            validation=root.with_name(root.name+'-runtime')/'agent/validation'
            self.assertEqual(list(validation.iterdir()),[])

    def test_restore_database_with_unexpected_schema_fails_in_uid_worker(self):
        from hue_admin.agent_snapshot import validate_agent_database_bytes
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            with sqlite3.connect(root/'state/commands.sqlite') as db:db.execute('CREATE TABLE unexpected(value TEXT)')
            with self.assertRaises(RuntimeError):
                validate_agent_database_bytes(root,'0.2.0-rc.6',(root/'state/commands.sqlite').read_bytes())

    def fixture(self,directory):
        base=Path(directory);base.chmod(0o755);root=base/'system'
        key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        files={'agent/hue_agent/__init__.py':b'# fixture','agent/hue_agent/__main__.py':b'# fixture',
               'agent/hue_agent/private_files.py':(Path(__file__).resolve().parents[1]/'hue_agent/private_files.py').read_bytes(),
               'manager/hue_admin/agent_runtime.py':b'# signed marker',
               'metadata/runtime-policy.json':b'{"schema":1,"agent":"dedicated","tunnel":"dedicated","childIdentity":true,"kernel":false}'}
        archive=base/'fixture.huerel';runtime=('mips-3.4','cp313',{'bumble':'0.0.236'})
        build_release(files,archive,key,'0.2.0-rc.6',*runtime,release_sequence=6)
        install_candidate(verify_release(archive,public,*runtime),root,'fresh')
        for name,value in (
            ('state/service-identities.json',{'hue-tunnel':{'schema':1,'uid':61000,'gid':61000},'hue-agent':{'schema':1,'uid':61001,'gid':61001}}),
            ('config/usb.json',{'schema':1,'vendor':'050d','product':'065a','serial':'AABBCCDDEEFF'}),
            ('config/device.json',{'name':'fixture'}),('secrets/bond.json',{'fixture':{'key':'dummy'}})):
            path=root/name;path.write_text(json.dumps(value));path.chmod(0o600)
        for name in ('api','device'):
            path=root/f'secrets/{name}.token';path.write_text('F'*40);path.chmod(0o600)
        config=agent_paths(root)
        config.update(transport='usb:050d:065a!',address='fixture-lamp',controller_public_address='fixture-adapter',
                      port=8787,cloud={'enabled':False},tunnel={'enabled':True,'port':8788,'token_file':str(root/'secrets/device.token')})
        path=root/'config/agent.json';path.write_text(json.dumps(config));path.chmod(0o600)
        with sqlite3.connect(root/'state/commands.sqlite') as db:
            db.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
            db.execute('CREATE TABLE sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)')
        (root/'state/commands.sqlite').chmod(0o600)
        return root

    def test_first_prepare_preserves_master_and_builds_private_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);master=(root/'config/agent.json').read_bytes()
            identity,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertEqual(identity['uid'],61001)
            self.assertEqual(root.stat().st_mode&0o777,0o700)
            self.assertEqual((root/'config/agent.json').read_bytes(),master)
            self.assertEqual(paths['keystore'].stat().st_uid,61001)
            self.assertEqual(paths['keystore'].stat().st_mode&0o777,0o600)
            self.assertEqual(paths['config'].stat().st_uid,0)
            self.assertEqual(paths['config'].stat().st_gid,61001)
            self.assertEqual(paths['config'].stat().st_mode&0o777,0o440)
            config=json.loads(paths['config'].read_text())
            self.assertEqual(config['transport'],'usb:050d:065a/AABBCCDDEEFF!')
            self.assertEqual(config['health_socket'],str(paths['health_socket']))

    def test_restart_does_not_overwrite_mutated_bond_or_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);_,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
            paths['keystore'].write_text('{"new":{"key":"dummy"}}')
            with sqlite3.connect(paths['journal']) as db:db.execute("INSERT INTO commands VALUES ('later','hash','{}',1)")
            prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertIn('new',json.loads(paths['keystore'].read_text()))
            with sqlite3.connect(paths['journal']) as db:self.assertEqual(db.execute('select id from commands').fetchall(),[('later',)])

    def test_ready_missing_journal_fails_without_reseeding_old_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);_,paths=prepare_agent_runtime(root,'0.2.0-rc.6');paths['journal'].unlink()
            with self.assertRaises((ValueError,FileNotFoundError)):prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertFalse(paths['journal'].exists())

    def test_interrupted_initial_seed_can_resume_before_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            def fail(stage):
                if stage=='seed:bond':raise OSError('fixture interruption')
            with self.assertRaises(OSError):prepare_agent_runtime(root,'0.2.0-rc.6',failpoint=fail)
            self.assertEqual(json.loads((root/'state/agent-runtime.json').read_text())['phase'],'prepared')
            prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertEqual(json.loads((root/'state/agent-runtime.json').read_text())['phase'],'ready')

    def test_unregistered_existing_data_is_not_erased(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);_,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
            (root/'state/agent-runtime.json').unlink();before=paths['keystore'].read_bytes()
            with self.assertRaises(ValueError):prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertEqual(paths['keystore'].read_bytes(),before)

    def test_unfinished_seed_retries_with_latest_legacy_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            def fail(stage):
                if stage=='seed:bond':raise OSError('fixture interruption')
            with self.assertRaises(OSError):prepare_agent_runtime(root,'0.2.0-rc.6',failpoint=fail)
            # The legacy release can keep serving commands after rollback.
            (root/'secrets/bond.json').write_text('{"latest":{"key":"dummy"}}')
            with sqlite3.connect(root/'state/commands.sqlite') as db:
                db.execute("INSERT INTO commands VALUES ('after-rollback','hash','{}',1)")
            _,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertIn('latest',json.loads(paths['keystore'].read_text()))
            with sqlite3.connect(paths['journal']) as db:
                self.assertEqual(db.execute('select id from commands').fetchall(),[('after-rollback',)])

    def _interrupted_runtime(self,directory):
        from test_sqlite_crash_recovery import hot_fixture
        root=self.fixture(directory);identity,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
        seed=Path(directory)/'interrupted';seed.mkdir(mode=0o700);source=hot_fixture(seed)
        for origin,target in ((source,paths['journal']),
                              (Path(str(source)+'-journal'),Path(str(paths['journal'])+'-journal'))):
            target.write_bytes(origin.read_bytes());target.chmod(0o600);os.chown(target,identity['uid'],identity['gid'])
        return root,paths

    def test_checkpoint_recovers_hot_runtime_and_reseed_keeps_data(self):
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory:
            root,paths=self._interrupted_runtime(directory)
            preserved={p:p.read_bytes() for p in (root/'secrets').iterdir() if p.is_file()}
            self.assertTrue(checkpoint_agent_state(root,'0.2.0-rc.6')['checkpointed'])
            self.assertEqual(json.loads((root/'state/agent-runtime.json').read_text())['phase'],'exported')
            self.assertEqual(len(list((root/'run').glob('sqlite-recovery-*'))),1)
            self.assertTrue(Path(str(paths['journal'])+'-journal').exists())
            prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertFalse(Path(str(paths['journal'])+'-journal').exists())
            with closing(sqlite3.connect(paths['journal'])) as db:
                self.assertEqual(db.execute('pragma quick_check').fetchone(),('ok',))
                self.assertEqual(db.execute('select count(*) from commands').fetchone()[0],128)
                self.assertEqual(db.execute('select last from sequences').fetchone()[0],99)
            self.assertEqual({p:p.read_bytes() for p in preserved},preserved)

    def test_checkpoint_rejects_live_supervisor_before_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root,paths=self._interrupted_runtime(directory);before=paths['journal'].read_bytes()
            with patch('hue_admin.service_manager.supervisor_owned',return_value=True):
                with self.assertRaisesRegex(ValueError,'AGENT_CHECKPOINT_REQUIRES_STOP'):
                    checkpoint_agent_state(root,'0.2.0-rc.6')
            self.assertEqual(paths['journal'].read_bytes(),before)
            self.assertEqual(list((root/'run').glob('sqlite-recovery-*')),[])

    def test_checkpoint_retries_after_recovery_before_marker_commit(self):
        from hue_admin import agent_runtime
        with tempfile.TemporaryDirectory() as directory:
            root,paths=self._interrupted_runtime(directory);real=agent_runtime.atomic_replace
            def interrupted(path,data):
                if Path(path)==root/'state/agent-runtime.json':raise OSError('fixture interruption')
                return real(path,data)
            with patch.object(agent_runtime,'atomic_replace',side_effect=interrupted):
                with self.assertRaises(OSError):checkpoint_agent_state(root,'0.2.0-rc.6')
            self.assertEqual(json.loads((root/'state/agent-runtime.json').read_text())['phase'],'ready')
            self.assertTrue(checkpoint_agent_state(root,'0.2.0-rc.6')['checkpointed'])
            self.assertEqual(json.loads((root/'state/agent-runtime.json').read_text())['phase'],'exported')
            self.assertEqual(len(list((root/'run').glob('sqlite-recovery-*'))),2)

    def test_reseed_retries_if_interrupted_after_archiving_hot_state(self):
        from contextlib import closing
        with tempfile.TemporaryDirectory() as directory:
            root,paths=self._interrupted_runtime(directory)
            checkpoint_agent_state(root,'0.2.0-rc.6')
            original=Path.rename
            def interrupted(path,destination):
                result=original(path,destination)
                if path==paths['state_dir']:raise OSError('fixture interruption after archive')
                return result
            with patch.object(Path,'rename',interrupted):
                with self.assertRaises(OSError):prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertFalse(paths['state_dir'].exists())
            self.assertEqual(json.loads((root/'state/agent-runtime.json').read_text())['phase'],'exported')
            prepare_agent_runtime(root,'0.2.0-rc.6')
            with closing(sqlite3.connect(paths['journal'])) as db:
                self.assertEqual(db.execute('select count(*) from commands').fetchone()[0],128)
            self.assertFalse(Path(str(paths['journal'])+'-journal').exists())
            self.assertEqual(len(list((root/'backups').glob('sqlite-runtime-*'))),1)

    def test_checkpoint_exports_current_state_for_legacy_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);_,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
            paths['keystore'].write_text('{"new":{"key":"dummy"}}')
            with sqlite3.connect(paths['journal']) as db:db.execute("INSERT INTO commands VALUES ('later','hash','{}',1)")
            checkpoint_agent_state(root,'0.2.0-rc.6')
            self.assertIn('new',json.loads((root/'secrets/bond.json').read_text()))
            self.assertEqual((root/'secrets/bond.json').stat().st_uid,0)
            with sqlite3.connect(root/'state/commands.sqlite') as db:self.assertEqual(db.execute('select id from commands').fetchall(),[('later',)])

    def test_reupgrade_after_legacy_rollback_keeps_new_legacy_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);_,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
            with sqlite3.connect(paths['journal']) as db:
                db.execute("INSERT INTO commands VALUES ('before-rollback','hash','{}',1)")
            checkpoint_agent_state(root,'0.2.0-rc.6')
            (root/'secrets/bond.json').write_text('{"after-rollback":{"key":"dummy"}}')
            with sqlite3.connect(root/'state/commands.sqlite') as db:
                db.execute("INSERT INTO commands VALUES ('after-rollback','hash','{}',2)")
            self.assertFalse(checkpoint_agent_state(root,'0.2.0-rc.6')['checkpointed'])
            prepare_agent_runtime(root,'0.2.0-rc.6')
            self.assertIn('after-rollback',json.loads(paths['keystore'].read_text()))
            with sqlite3.connect(paths['journal']) as db:
                self.assertEqual(db.execute('select id from commands order by created').fetchall(),
                                 [('before-rollback',),('after-rollback',)])

    def test_real_limited_uid_reads_projected_config_and_private_state(self):
        from hue_admin.privileges import drop_identity
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);identity,paths=prepare_agent_runtime(root,'0.2.0-rc.6')
            def drop():drop_identity(identity['uid'],identity['gid'])
            code="import json,os,sys; from hue_agent.private_files import private_file; c=json.loads(private_file(sys.argv[1]).read_text()); assert len(private_file(c['token_file']).read_text())==40; private_file(c['keystore']); assert not os.access(sys.argv[2],os.X_OK); assert '' not in sys.path; print('PRIVATE_RUNTIME_OK')"
            result=subprocess.run([sys.executable,'-s','-P','-u','-c',code,str(paths['config']),str(root)],
                cwd=paths['state_dir'],env=agent_environment(paths,'0.2.0-rc.6'),preexec_fn=drop,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('PRIVATE_RUNTIME_OK',result.stdout)
