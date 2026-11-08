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
