import tempfile
import unittest
from pathlib import Path

from hue_admin.restore_transaction import apply_files, recover


class PowerCut(BaseException):
    pass


class RestoreTransactionTests(unittest.TestCase):
    def fixture(self, root):
        live=Path(root)/'live';live.mkdir()
        targets={}
        for name in ('config/agent.json','secrets/api.token'):
            p=live/name.replace('/','-');p.write_bytes(b'old');p.chmod(0o600);targets[name]=p
        return targets,Path(root)/'transaction'

    def test_success_requires_stop_before_writes_and_health_after_start(self):
        with tempfile.TemporaryDirectory() as root:
            targets,transaction=self.fixture(root);events=[]
            def stop():
                events.append('stop')
                self.assertTrue(all(p.read_bytes()==b'old' for p in targets.values()))
                return True
            apply_files({n:b'new' for n in targets},targets,transaction,stop,lambda:events.append('start') or True,lambda:True)
            self.assertEqual(events,['stop','start'])
            self.assertTrue(all(p.read_bytes()==b'new' for p in targets.values()))

    def test_stop_failure_does_not_replace_anything(self):
        with tempfile.TemporaryDirectory() as root:
            targets,transaction=self.fixture(root)
            with self.assertRaises(RuntimeError):
                apply_files({n:b'new' for n in targets},targets,transaction,lambda:False,lambda:True,lambda:True)
            self.assertTrue(all(p.read_bytes()==b'old' for p in targets.values()))

    def test_failed_health_rolls_back_before_starting_old_version(self):
        with tempfile.TemporaryDirectory() as root:
            targets,transaction=self.fixture(root);starts=[]
            def start():
                starts.append([p.read_bytes() for p in targets.values()]);return True
            with self.assertRaises(RuntimeError):
                apply_files({n:b'new' for n in targets},targets,transaction,lambda:True,start,lambda:False)
            self.assertEqual(starts,[[b'new',b'new'],[b'old',b'old']])
            self.assertTrue(all(p.read_bytes()==b'old' for p in targets.values()))

    def test_each_power_cut_point_recovers_old_files(self):
        for point in ('prepared','ready','write:config/agent.json','write:secrets/api.token','started'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as root:
                targets,transaction=self.fixture(root)
                def cut(phase):
                    if phase==point:raise PowerCut()
                with self.assertRaises(PowerCut):
                    apply_files({n:b'new' for n in targets},targets,transaction,lambda:True,lambda:True,lambda:True,failpoint=cut)
                recover(targets,transaction,lambda:True,lambda:True)
                self.assertTrue(all(p.read_bytes()==b'old' for p in targets.values()))

    def test_tampered_recovery_file_is_refused_before_any_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            targets,transaction=self.fixture(root)
            def cut(phase):
                if phase=='ready':raise PowerCut()
            with self.assertRaises(PowerCut):
                apply_files({n:b'new' for n in targets},targets,transaction,lambda:True,lambda:True,lambda:True,failpoint=cut)
            (transaction/'before/config/agent.json').write_bytes(b'tampered')
            with self.assertRaises(ValueError):recover(targets,transaction,lambda:True,lambda:True)
            self.assertTrue(all(p.read_bytes()==b'old' for p in targets.values()))
