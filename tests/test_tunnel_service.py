"""Native POSIX service lifecycle tests; also run on the actual Keenetic ash."""
import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix', 'requires native POSIX process and flock semantics')
class TunnelServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hue-service-test-')
        self.base = Path(self.temp.name)
        self.service = self.base / 'S98hue-tunnel'
        for name in ('S98hue-tunnel', 'tunnel-launch.py'):
            source = (ROOT / 'deploy' / name).read_text()
            source = source.replace('/opt/var/lib/hue-tunnel', str(self.base))
            source = source.replace('/opt/etc/init.d/S98hue-tunnel', str(self.service))
            if name == 'S98hue-tunnel':
                source = source.replace('/opt/bin/python3', os.sys.executable)
            target = self.base / name
            target.write_text(source)
            target.chmod(0o700)
        (self.base / 'tunnel.token').write_text('fixture-not-a-real-token')
        # First startup simulates DNS not ready; subsequent startups remain alive.
        (self.base / 'cloudflared').write_text('''#!/bin/sh
base="$(dirname "$0")"
n=0; [ ! -f "$base/starts" ] || n=$(cat "$base/starts")
n=$((n+1)); echo "$n" > "$base/starts"
[ "$n" -gt 1 ] || exit 1
trap 'exit 0' TERM INT
while :; do sleep 1; done
''')
        (self.base / 'cloudflared').chmod(0o700)

    def tearDown(self):
        if self.service.exists():
            subprocess.run([str(self.service), 'stop'], capture_output=True, timeout=25)
        self.temp.cleanup()

    def command(self, action):
        return subprocess.run([str(self.service), action], capture_output=True, text=True, timeout=30)

    def wait_for(self, predicate, timeout=12):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.1)
        self.fail('service did not reach expected state')

    def starts(self):
        try:
            return int((self.base / 'starts').read_text())
        except (OSError, ValueError):
            return 0

    def test_startup_failure_retries_and_child_crash_recovers(self):
        self.assertEqual(self.command('start').returncode, 0)
        self.wait_for(lambda: self.starts() >= 2)
        self.assertEqual(self.command('status').returncode, 0)
        child = int((self.base / 'tunnel.pid').read_text())
        os.kill(child, signal.SIGKILL)
        self.wait_for(lambda: self.starts() >= 3)
        self.assertEqual(self.command('stop').returncode, 0)
        count = self.starts()
        time.sleep(3)
        self.assertEqual(self.starts(), count)
        self.assertNotEqual(self.command('status').returncode, 0)

    def test_concurrent_starts_create_only_one_supervisor(self):
        a = subprocess.Popen([str(self.service), 'start'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        b = subprocess.Popen([str(self.service), 'start'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(a.wait(timeout=30), 0)
        self.assertEqual(b.wait(timeout=30), 0)
        self.wait_for(lambda: self.starts() >= 2)
        time.sleep(3)
        self.assertEqual(self.starts(), 2)


if __name__ == '__main__':
    unittest.main()
