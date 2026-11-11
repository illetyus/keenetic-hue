"""Run on native Keenetic ash; the fake agent never owns the USB adapter."""
import test_tunnel_service as tunnel_tests
import os
import tempfile
import signal
import subprocess
import time
from pathlib import Path
import unittest


@unittest.skipUnless(os.name == 'posix', 'requires native POSIX process ownership')
class UnifiedAgentTests(tunnel_tests.TunnelServiceTests):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='hue-agent-supervision-')
        self.base=Path(self.temp.name);self.service=self.base/'S99hue-agent-supervised'
        root=Path(__file__).resolve().parents[1]
        for name in ('S99hue-agent-supervised','agent-supervisor-launch.py'):
            source=(root/'deploy'/name).read_text()
            source=source.replace('/opt/var/lib/hue-agent',str(self.base))
            source=source.replace('/opt/etc/init.d/S99hue-agent-supervised',str(self.service))
            source=source.replace('agent-child.pid','tunnel.pid')
            source=source.replace("BASE / 'app' / 'agent-foreground.py'", "BASE / 'fake-agent.py'")
            if name=='S99hue-agent-supervised':
                source=source.replace('/opt/bin/python3',os.sys.executable)
                source=source.replace('"$BASE/app/agent-foreground.py"', '"$BASE/fake-agent.py"')
            target=self.base/name;target.write_text(source);target.chmod(0o700)
        # Existing fixture tests read tunnel.pid; alias the test-only filename.
        source=self.service.read_text().replace('agent-child.pid','tunnel.pid')
        self.service.write_text(source)
        (self.base/'fake-agent.py').write_text('''import time,signal
from pathlib import Path
p=Path(__file__).parent/'starts'
n=int(p.read_text())+1 if p.exists() else 1
p.write_text(str(n))
if n==1:raise SystemExit(1)
signal.signal(signal.SIGTERM,lambda *_:exit(0))
while True:time.sleep(0.1)
''')

    def test_twenty_starts_and_thirty_second_manual_stop(self):
        children=[subprocess.Popen([str(self.service),'start'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL) for _ in range(20)]
        for child in children:self.assertEqual(child.wait(timeout=45),0)
        self.wait_for(lambda:self.starts()>=2)
        time.sleep(2)
        self.assertEqual(self.starts(),2)
        self.assertEqual(self.command('stop').returncode,0)
        count=self.starts();time.sleep(30)
        self.assertEqual(self.starts(),count)

    def test_supervisor_death_stop_reaps_only_its_orphan(self):
        self.assertEqual(self.command('start').returncode,0)
        self.wait_for(lambda:self.starts()>=2)
        child=int((self.base/'tunnel.pid').read_text())
        supervisor=int((self.base/'supervisor.pid').read_text())
        try:
            os.kill(supervisor,signal.SIGKILL)
            time.sleep(0.2)
            self.assertEqual(self.command('stop').returncode,0)
            def exited():
                try:return Path(f'/proc/{child}/stat').read_text().split(') ',1)[1].startswith('Z')
                except FileNotFoundError:return True
            self.wait_for(exited,3)
        finally:
            try:os.kill(child,signal.SIGTERM)
            except ProcessLookupError:pass

    def test_unrelated_child_pid_is_never_signalled(self):
        victim=subprocess.Popen(['sleep','60'])
        try:
            (self.base/'tunnel.pid').write_text(str(victim.pid))
            self.assertNotEqual(self.command('stop').returncode,0)
            self.assertIsNone(victim.poll())
        finally:
            victim.terminate();victim.wait(timeout=5)
            (self.base/'tunnel.pid').unlink(missing_ok=True)


class LauncherUnitTests(unittest.TestCase):
    def _load(self, name):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / 'deploy' / name
        spec = importlib.util.spec_from_file_location(name.replace('-', '_').replace('.', '_'), path)
        mod = importlib.util.module_from_spec(spec)
        from unittest.mock import MagicMock, patch
        with patch.dict('sys.modules', {'fcntl': MagicMock()}):
            spec.loader.exec_module(mod)
        return mod

    def test_agent_supervisor_stop_orphan_handles_non_utf8_and_lock_retry(self):
        import sys
        from unittest.mock import MagicMock, patch
        mod = self._load('agent-supervisor-launch.py')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'agent-child.pid').write_text('12345')
            with patch.object(mod, 'BASE', base):
                def fake_read_text(p):
                    if p == base / 'agent-child.pid':
                        return '12345'
                    return '12345 (sh) S 1 1 1 0 -1 0 0 0 0 0 0 0 0 20 0 1 0 999 0 0'
                with patch.object(Path, 'read_text', fake_read_text), \
                     patch.object(Path, 'read_bytes', return_value=b'\xff\xfe\x00'):
                    self.assertFalse(mod.stop_orphan())
            fake_fcntl = MagicMock()
            fake_fcntl.LOCK_EX = 2; fake_fcntl.LOCK_NB = 4
            fake_fcntl.flock.side_effect = [BlockingIOError(), None]
            with patch.dict('sys.modules', {'fcntl': fake_fcntl}), \
                 patch.object(mod, 'fcntl', fake_fcntl), \
                 patch.object(mod, 'BASE', base), \
                 patch.object(mod, 'stop_orphan', return_value=True), \
                 patch.object(mod.os, 'open', return_value=10), \
                 patch.object(mod.os, 'close'), \
                 patch.object(mod.time, 'sleep'), \
                 patch.object(sys, 'argv', ['agent-supervisor-launch.py', '--stop-orphan']):
                self.assertEqual(mod.main(), 0)
            self.assertEqual(fake_fcntl.flock.call_count, 2)

    def test_tunnel_launch_creates_base_directory(self):
        from unittest.mock import MagicMock, patch
        mod = self._load('tunnel-launch.py')
        fake_fcntl = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            missing_base = Path(tmp) / 'hue-tunnel'
            with patch.dict('sys.modules', {'fcntl': fake_fcntl}), \
                 patch.object(mod, 'fcntl', fake_fcntl), \
                 patch.object(mod, 'BASE', missing_base), \
                 patch.object(mod.os, 'open', return_value=10), \
                 patch.object(mod.os, 'close'), \
                 patch.object(mod.os, 'dup2'), \
                 patch.object(mod.os, 'set_inheritable'), \
                 patch.object(mod.subprocess, 'Popen'):
                self.assertEqual(mod.main(), 0)
            self.assertTrue(missing_base.is_dir())
