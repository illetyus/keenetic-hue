import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from hue_admin.acceptance_journal import AcceptanceJournal
from test_acceptance_recovery import snapshot


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Native root journal')
class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        (self.root / 'state').mkdir(mode=0o700)
        self.store = AcceptanceJournal(self.root)
        self.value = snapshot()
        self.value.update(phase='prepared', supervisorPid=os.getpid())
        del self.value['workerPid']

    def test_private_durable_roundtrip_and_live_supervisor_blocks(self):
        self.store.save(self.value)
        self.assertEqual(self.store.load(), self.value)
        self.assertEqual((self.root/'state/acceptance-current.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.store.quiescent(self.value))

    def test_prior_boot_is_quiescent_without_pid_reuse_signals(self):
        with patch('hue_admin.acceptance_journal.boot_id', return_value='11111111-1111-1111-1111-111111111111'):
            self.store.save(self.value)
        self.assertTrue(self.store.quiescent(self.value))

    def test_same_boot_dead_owner_but_remaining_group_blocks(self):
        self.store.save(self.value)
        self.value.update(phase='running', workerPid=os.getpid())
        # Simulate a dedicated worker token without changing this test process's group.
        from hue_admin import acceptance_journal as module
        actual = module.process_token
        with patch.object(module, 'process_token', side_effect=lambda pid: {**actual(pid), 'pgid': pid}):
            self.store.save(self.value)
        with patch.object(module, 'process_token', return_value=None), patch.object(module, '_live_group', return_value=[999]):
            self.assertFalse(self.store.quiescent(self.value))
        with patch.object(module, 'process_token', return_value=None), patch.object(module, '_live_group', return_value=[]):
            self.assertTrue(self.store.quiescent(self.value))

    def test_pending_operation_and_changed_baseline_cannot_be_overwritten(self):
        self.store.save(self.value)
        different = snapshot()
        different.update(phase='prepared', supervisorPid=os.getpid())
        del different['workerPid']
        with self.assertRaises(ValueError):
            self.store.save(different)
        changed = copy.deepcopy(self.value)
        changed['baselinePhysical']['on'] = True
        with self.assertRaises(ValueError):
            self.store.save(changed)
        self.assertEqual(self.store.load(), self.value)

    def test_unsafe_file_and_duplicate_json_are_rejected(self):
        self.store.save(self.value)
        path = self.root/'state/acceptance-current.json'
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            self.store.load()
        path.chmod(0o600)
        path.write_text('{"schema":1,"schema":1}')
        with self.assertRaises(ValueError):
            self.store.load()
        path.unlink()
        path.symlink_to('/etc/passwd')
        with self.assertRaises((ValueError, OSError)):
            self.store.save(self.value)

    def adapter(self):
        from test_acceptance_recovery import Adapter
        path = self.root/'fixture-release'
        path.write_text('0.2.0-rc.5')
        adapter = Adapter()
        adapter.selected = lambda: path.read_text()
        adapter.healthy = lambda expected: path.read_text() == expected
        adapter.update = lambda candidate: path.write_text(candidate)
        adapter.rollback = lambda: path.write_text('0.2.0-rc.5')
        adapter.preflight = lambda _: None
        adapter.candidate_checks = lambda: True
        return adapter

    def test_real_watchdog_journal_and_controller_roundtrip(self):
        from hue_admin.controlled_acceptance import run_controlled
        result = run_controlled(self.adapter(), '0.2.0-rc.5', '0.2.0-rc.7',
                                self.store.save, self.value['operationId'], 3)
        self.assertEqual(self.store.load(), result)
        self.assertEqual(result['phase'], 'closed')
        self.assertTrue(result['restoreVerified'])

    def test_supervisor_loss_terminates_worker_then_recovers_without_replay(self):
        import signal
        from hue_admin.controlled_acceptance import run_controlled
        from hue_admin.acceptance_recovery import recover_interrupted
        from hue_admin.acceptance_watchdog import _live_group
        adapter = self.adapter()
        ready = self.root/'fixture-worker-ready'
        def checks():
            ready.touch()
            time.sleep(20)
            return True
        adapter.candidate_checks = checks
        supervisor = os.fork()
        if supervisor == 0:
            try:
                run_controlled(adapter, '0.2.0-rc.5', '0.2.0-rc.7', self.store.save,
                               self.value['operationId'], 25)
            except BaseException:
                import traceback
                traceback.print_exc()
                os._exit(1)
            os._exit(0)
        worker = None
        reaped = False
        try:
            deadline = time.monotonic() + 5
            while not ready.exists():
                if time.monotonic() > deadline:
                    observed=self.store.load()
                    self.fail('fixture worker did not start: phase='+observed['phase']+' outcome='+str(observed['outcome']))
                time.sleep(.02)
            state = self.store.load()
            worker = state['workerPid']
            os.kill(supervisor, signal.SIGKILL)
            os.waitpid(supervisor, 0)
            reaped = True
            deadline = time.monotonic() + 5
            while _live_group(worker):
                if time.monotonic() > deadline:
                    self.fail('fixture worker still live')
                time.sleep(.02)
            result = recover_interrupted(adapter, self.store, self.store.quiescent)
            self.assertEqual(result['phase'], 'closed')
            self.assertEqual(result['outcome'], 'interrupted')
            self.assertEqual(adapter.selected(), '0.2.0-rc.5')
        finally:
            if not reaped:
                os.kill(supervisor, signal.SIGKILL)
                os.waitpid(supervisor, 0)
            if worker is not None and _live_group(worker):
                os.killpg(worker, signal.SIGKILL)


if __name__ == '__main__':
    unittest.main()
