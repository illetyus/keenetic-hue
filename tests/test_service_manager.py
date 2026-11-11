"""Native isolated service ownership; fake agent never opens the USB adapter."""
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import os
from pathlib import Path
import signal
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from hue_admin import service_manager as manager


@unittest.skipUnless(os.name=='posix','Native process/flock test')
class ServiceManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hue-unified-owner-');self.root=Path(self.temp.name)
        for name in ('run','logs','state','secrets','config','releases'): (self.root/name).mkdir(mode=0o700)
        release=self.root/'releases/0.2.0';release.mkdir(mode=0o700)
        for name in ('service','agent','manager','bin'):(release/name).mkdir(mode=0o700)
        package=release/'manager/hue_admin';package.mkdir(mode=0o700)
        source=Path(__file__).resolve().parents[1]
        for path in (source/'hue_admin').glob('*.py'):(package/path.name).write_bytes(path.read_bytes())
        for name in ('hue-supervise.sh','component-child.py'):
            (release/'service'/name).write_bytes((source/'deploy'/name).read_bytes())
        self.python=patch.object(manager,'PYTHON',sys.executable);self.python.start()
        self.verify=patch.object(manager,'verify_selected',return_value='0.2.0');self.verify.start()
        (self.root/'active.json').write_text('{"schema":1,"releaseId":"0.2.0"}');(self.root/'active.json').chmod(0o600)
        config=manager.agent_paths(self.root)
        config.update({'transport':'usb:fixture','address':'fixture-lamp','controller_public_address':'fixture-adapter',
                       'port':8787,'cloud':{'enabled':False},'tunnel':{'enabled':True,'port':8788,'token_file':str(self.root/'secrets/device.token')}})
        (self.root/'config/agent.json').write_text(json.dumps(config));(self.root/'config/agent.json').chmod(0o600)
        agent=release/'agent/hue_agent';agent.mkdir(mode=0o700);(agent/'__init__.py').write_text('')
        fake='''import os,time,signal,sys,json,fcntl
from pathlib import Path
cfg=json.loads(Path(sys.argv[-1]).read_text());root=Path(cfg['journal']).parent.parent
lock=(root/'run/adapter.lock').open('a+')
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    (root/'state/duplicate-owner').write_text('duplicate');raise SystemExit(75)
counter=root/'state/agent-starts';n=int(counter.read_text())+1 if counter.exists() else 1
temporary=counter.with_name(counter.name+'.'+str(os.getpid()));temporary.write_text(str(n));temporary.replace(counter)
if n==1:raise SystemExit(1)
(root/'run/agent.pid').write_text(str(os.getpid()))
signal.signal(signal.SIGTERM,lambda *_:exit(0))
while True:time.sleep(.1)
'''
        (agent/'__main__.py').write_text(fake)
        tunnel=release/'bin/cloudflared'
        # Native Python is an ELF stand-in, not a shell-script loophole in the
        # production ownership verifier. Its first argument "tunnel" is a
        # fixture script in its private cwd. No actual Tunnel/network is used.
        shutil.copyfile(Path(sys.executable).resolve(),tunnel);tunnel.chmod(0o700)
        self.runtime=self.root.with_name(self.root.name+'-runtime')
        if os.geteuid()==0:
            import zipfile
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
            from hue_admin.releases import build_release
            from hue_admin.account_store import _ensure_accounts
            from hue_admin.privileges import prepare_tunnel_runtime
            tables={}
            for name in ('native_passwd','native_group','entware_passwd','entware_group'):
                path=self.root/name;path.write_text('root:*:0:0::/root:/bin/sh\n' if name.endswith('passwd') else 'root:*:0:\n');path.chmod(0o600);tables[name]=path
            profiles=_ensure_accounts(self.root,tables)
            code=(package/'account_store.py').read_text()
            code += '\nSYSTEM=Path('+repr(str(self.root))+')\nACCOUNT_PATHS={'+','.join(repr(k)+':Path('+repr(str(v))+')' for k,v in tables.items())+'}\n'
            (package/'account_store.py').write_text(code)
            data=tunnel.read_bytes()
            key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
            trust=self.root/'config/release-trust.pub';trust.write_bytes(public);trust.chmod(0o600)
            archive=self.root/'state/fixture.huerel'
            build_release({'bin/cloudflared':data,'manager/hue_admin/privileges.py':(package/'privileges.py').read_bytes()},archive,key,'0.2.0','mips-3.4','cp313',{'bumble':'0.0.236'},release_sequence=5)
            with zipfile.ZipFile(archive) as bundle:manifest_data=bundle.read('manifest.json')
            manifest=release/'verified-manifest.json';manifest.write_bytes(manifest_data);manifest.chmod(0o600)
            token=self.root/'secrets/tunnel.token';token.write_text('fixture-only');token.chmod(0o600)
            identity,paths=prepare_tunnel_runtime(self.root,'0.2.0')
            (paths['data']/'tunnel').write_text('import signal,time\nsignal.signal(signal.SIGTERM,lambda *_:exit(0))\nwhile True:time.sleep(.1)\n')
            (paths['data']/'tunnel').chmod(0o444)
            self.tunnel_identity=identity

    def tearDown(self):
        for component in ('tunnel','agent'):manager.action(component,'stop',self.root)
        self.verify.stop();self.python.stop();self.temp.cleanup()
        if self.runtime.exists():shutil.rmtree(self.runtime)

    def wait(self,predicate,seconds=15):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate():return
            time.sleep(.1)
        self.fail('Owner condition did not become true')

    def starts(self):
        path=self.root/'state/agent-starts'
        return int(path.read_text()) if path.exists() else 0

    @unittest.skipUnless(hasattr(os,'geteuid') and os.geteuid()==0,'Native privilege drop requires root')
    def test_twenty_starts_independent_restart_and_stop(self):
        with ThreadPoolExecutor(max_workers=20) as pool:
            self.assertTrue(all(pool.map(lambda _:manager.action('agent','start',self.root),range(20))))
        self.wait(lambda:self.starts()>=2);time.sleep(.5);self.assertEqual(self.starts(),2)
        self.assertFalse((self.root/'state/duplicate-owner').exists())
        self.assertTrue(manager.action('tunnel','start',self.root))
        self.wait(lambda:manager.child_pid('tunnel',self.root) is not None)
        tunnel=manager.child_pid('tunnel',self.root)
        from hue_admin.privileges import process_matches_identity
        self.wait(lambda:process_matches_identity(Path(f'/proc/{tunnel}/status').read_text(),self.tunnel_identity['uid'],self.tunnel_identity['gid']))
        self.assertTrue(manager.child_owned('tunnel',self.root,manager.owner_record('tunnel',self.root),tunnel))
        self.assertTrue(manager.action('agent','restart',self.root));self.wait(lambda:self.starts()>=3)
        self.assertEqual(manager.child_pid('tunnel',self.root),tunnel)
        self.assertTrue(manager.action('agent','stop',self.root));count=self.starts();time.sleep(3)
        self.assertEqual(self.starts(),count)

    def test_orphan_recovery_and_old_boot_pid_never_signals_victim(self):
        self.assertTrue(manager.action('agent','start',self.root));self.wait(lambda:self.starts()>=2)
        record=manager.owner_record('agent',self.root);old_child=manager.child_pid('agent',self.root)
        from hue_admin.lifecycle_events import emit
        self.assertTrue(emit(self.root,'agent','operation_failed','owner_wait','OWNER_WAIT_TIMEOUT',release_id='0.2.0',attempt=record))
        os.kill(record['pid'],signal.SIGKILL)
        self.wait(lambda:not manager.supervisor_owned('agent',self.root,record))
        from hue_admin.lifecycle_events import unavailable_reason
        self.assertEqual(unavailable_reason(self.root,'agent',record,'0.2.0'),'SUPERVISOR_EXIT_UNKNOWN')
        self.assertTrue(manager.action('agent','stop',self.root))
        self.assertIsNone(manager.child_pid('agent',self.root))
        import subprocess
        victim=subprocess.Popen(['sleep','60'])
        try:
            stale={**record,'pid':victim.pid,'bootId':'00000000-0000-0000-0000-000000000000'}
            (self.root/'run/agent-supervisor.json').write_text(json.dumps(stale));(self.root/'run/agent-supervisor.json').chmod(0o600)
            (self.root/'run/agent-child.pid').write_text(str(victim.pid))
            self.assertTrue(manager.action('agent','start',self.root));self.wait(lambda:self.starts()>=3)
            self.assertIsNone(victim.poll())
        finally:victim.terminate();victim.wait(timeout=5)

    def test_unknown_component_cannot_launch(self):
        self.assertFalse(manager.action('agent;reboot','start',self.root))

    def test_observed_supervisor_signals_have_matching_terminal_identity(self):
        from hue_admin.lifecycle_events import unavailable_reason
        for signame,number in (('TERM',signal.SIGTERM),('INT',signal.SIGINT)):
            with self.subTest(signal=signame):
                self.assertTrue(manager.action('agent','start',self.root))
                self.wait(lambda:self.starts()>=2)
                record=manager.owner_record('agent',self.root)
                from hue_admin.lifecycle_events import emit, last_failure
                self.assertTrue(emit(self.root,'agent','operation_failed','owner_wait','OWNER_WAIT_TIMEOUT',release_id='0.2.0'))
                self.assertTrue(manager.action('agent','start',self.root))
                self.assertIsNone(last_failure(self.root,'agent'))
                self.assertTrue(emit(self.root,'agent','operation_failed','owner_wait','OWNER_WAIT_TIMEOUT',release_id='0.2.0'))
                os.kill(record['pid'],number)
                self.wait(lambda:(self.root/'run/agent-terminal.json').exists() and
                          json.loads((self.root/'run/agent-terminal.json').read_text())['pid']==record['pid'])
                self.wait(lambda:not manager.supervisor_owned('agent',self.root,record))
                terminal=json.loads((self.root/'run/agent-terminal.json').read_text())
                for key in ('pid','startTicks','bootId','releaseId'):self.assertEqual(terminal[key],record[key])
                self.assertEqual(terminal['signal'],signame)
                self.assertEqual(unavailable_reason(self.root,'agent',record,'0.2.0'),'SUPERVISOR_'+signame)
                rows=[json.loads(line) for line in (self.root/'logs/agent-supervisor.log').read_text().splitlines()]
                matching=[row for row in rows if row['pid']==record['pid']]
                self.assertTrue({'supervisor_start','supervisor_signal','supervisor_exit'} <= {row['event'] for row in matching})
                self.assertTrue(all('utc' in row and 'monotonic' in row for row in matching))
                self.assertTrue(manager.action('agent','stop',self.root))

    def test_diagnostic_log_failure_does_not_break_start_or_stop(self):
        victim=self.root/'state/log-victim';victim.write_text('unchanged')
        (self.root/'logs/lifecycle.log').symlink_to(victim)
        self.assertTrue(manager.action('agent','start',self.root));self.wait(lambda:self.starts()>=2)
        self.assertFalse((self.root/'state/duplicate-owner').exists())
        self.assertTrue(manager.action('agent','stop',self.root))
        self.assertEqual(victim.read_text(),'unchanged')

    def test_fifo_supervisor_log_cannot_block_start_or_stop(self):
        log=self.root/'logs/agent-supervisor.log'
        os.mkfifo(log,0o600)
        self.assertTrue(manager.action('agent','start',self.root));self.wait(lambda:self.starts()>=2)
        self.assertTrue(manager.action('agent','stop',self.root))
        self.assertTrue(log.is_fifo())

    def test_supervisor_log_rotates_and_invalid_rotation_target_stays_bounded(self):
        log=self.root/'logs/agent-supervisor.log'
        log.write_text(' '*64000);log.chmod(0o600)
        self.assertTrue(manager.action('agent','start',self.root));self.wait(lambda:self.starts()>=2)
        self.assertTrue(manager.action('agent','stop',self.root))
        previous=log.with_suffix('.log.previous')
        self.assertEqual(previous.stat().st_size,64000)
        self.assertLessEqual(log.stat().st_size,65536)
        previous.unlink();previous.mkdir()
        log.write_text(' '*64000)
        self.assertTrue(manager.action('agent','start',self.root));self.wait(lambda:self.starts()>=3)
        self.assertTrue(manager.action('agent','stop',self.root))
        self.assertEqual(log.stat().st_size,64000)
        self.assertEqual(list(previous.iterdir()),[])

    def test_isolated_child_does_not_mutate_verified_manager_files(self):
        self.assertTrue(manager.action('agent','start',self.root));self.wait(lambda:self.starts()>=2)
        self.assertEqual(list((self.root/'releases/0.2.0/manager').rglob('__pycache__')),[])

    def test_launch_preserves_an_unrelated_open_descriptor(self):
        victim=os.open(self.root/'state/victim',os.O_CREAT|os.O_RDWR,0o600)
        saved=None
        try:
            try:saved=os.dup(9)
            except OSError:pass
            os.dup2(victim,9);expected=os.fstat(9).st_ino
            def launch(*args,**kwargs):
                self.assertEqual(os.fstat(9).st_ino,expected,'launch temporarily replaced another thread descriptor')
            with manager.file_lock(self.root/'run/fixture.lock') as fd,patch.object(manager.subprocess,'Popen',side_effect=launch):
                manager.spawn_supervisor('agent',self.root,'0.2.0',fd)
        finally:
            if saved is None:os.close(9)
            else:os.dup2(saved,9);os.close(saved)
            os.close(victim)


class ServiceManagerCleanupTests(unittest.TestCase):
    def test_cleanup_failed_spawn_pops_already_exited_child(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'run').mkdir();(root/'logs').mkdir()
            (root/'active.json').write_text('{"schema":1,"releaseId":"0.2.0"}')
            dead_child=MagicMock()
            dead_child.pid=43210
            dead_child.poll.return_value=1
            with patch.object(manager,'selection',return_value='0.2.0'), \
                 patch.object(manager,'owner_record',return_value=None), \
                 patch.object(manager,'supervisor_owned',return_value=False), \
                 patch.object(manager,'file_lock'), \
                 patch.object(manager,'stop_orphan',return_value=True), \
                 patch.object(manager,'finalize_stopped_agent'), \
                 patch.object(manager,'verify_selected',return_value='0.2.0'), \
                 patch.object(manager,'emit'), \
                 patch.object(manager,'process',return_value=None), \
                 patch.object(manager.subprocess,'Popen',return_value=dead_child), \
                 patch.object(manager.time,'monotonic',side_effect=[0.0,10.0]):
                with manager._spawned_lock:
                    manager._spawned.clear()
                result=manager.start_locked('agent',root)
                self.assertFalse(result)
                with manager._spawned_lock:
                    self.assertNotIn((str(root),'agent'),manager._spawned)
                dead_child.wait.assert_called_once()
