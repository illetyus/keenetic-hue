import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from hue_admin.acceptance_launcher import detach, launch_candidate, run_guardian


class InputTests(unittest.TestCase):
    def test_final_callback_runs_after_both_children_and_preserves_failed_operation(self):
        done=Mock()
        with patch('hue_admin.acceptance_launcher._child',side_effect=[1,0]) as child:
            self.assertEqual(run_guardian(lambda:True,lambda:True,1,1,on_exit=done),1)
            self.assertEqual(child.call_count,2)
            done.assert_called_once_with(1)

    def test_invalid_timeouts_fail_before_fork(self):
        for value in (0, -1, True, float('nan'), float('inf'), 1201):
            with self.subTest(value=value), self.assertRaises(ValueError):
                detach(lambda: True, lambda: True, timeout=value)


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Native root detached fixture')
class NativeTests(unittest.TestCase):
    def wait(self, pid, deadline=10):
        until=time.monotonic()+deadline
        while True:
            result=os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
            if result is not None:
                os.waitpid(pid,0)
                return result.si_status if result.si_code==os.CLD_EXITED else -1
            if time.monotonic()>until:
                os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)
                self.fail('fixture guardian did not finish')
            time.sleep(.02)

    def test_detached_session_closes_inherited_descriptors(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'result'
            fd=os.open(Path(directory)/'inherited',os.O_RDWR|os.O_CREAT,0o600)
            def run():
                try:os.fstat(fd)
                except OSError:closed=True
                else:closed=False
                path.write_text(str(os.getsid(0))+' '+str(closed))
                return True
            try:
                pid=detach(run,lambda:True,timeout=2,recovery_timeout=2)
                self.assertEqual(self.wait(pid),0)
                self.assertEqual(path.read_text(),str(pid)+' True')
            finally:os.close(fd)

    def test_ssh_like_parent_exit_does_not_kill_guardian(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'finished'
            parent=os.fork()
            if parent==0:
                def run():time.sleep(.15);marker.touch();return True
                detach(run,lambda:True,timeout=2,recovery_timeout=2)
                os._exit(0)
            os.waitpid(parent,0)
            deadline=time.monotonic()+3
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(marker.exists())

    def test_timeout_stops_direct_child_before_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            late=Path(directory)/'late'
            recovered=Path(directory)/'recovered'
            def run():time.sleep(.4);late.touch();return True
            def recover():recovered.touch();return True
            pid=detach(run,recover,timeout=.1,recovery_timeout=2)
            self.assertEqual(self.wait(pid),124)
            time.sleep(.5)
            self.assertFalse(late.exists());self.assertTrue(recovered.exists())

    def test_failed_run_is_not_success_when_recovery_passes(self):
        self.assertEqual(run_guardian(lambda:False,lambda:True,1,1),1)

    def test_missing_journal_never_acknowledges_recovery(self):
        journal=Mock()
        journal.load.side_effect=FileNotFoundError('fixture')
        from test_acceptance_recovery import Adapter
        result={'phase':'closed','restoreVerified':True,'outcome':'completed'}
        with patch('hue_admin.controlled_acceptance.run_controlled',return_value=result):
            pid=launch_candidate(Adapter(),journal,'0.2.0-rc.5','0.2.0-rc.7',str(uuid.uuid4()),timeout=1)
            self.assertEqual(self.wait(pid),1)

    def test_recovery_timeout_is_not_success(self):
        self.assertEqual(run_guardian(lambda:True,lambda:time.sleep(5),1,.1),124)

    def test_unconfirmed_child_stop_blocks_recovery_without_blocking_waitpid(self):
        from contextlib import ExitStack
        import hue_admin.acceptance_launcher as module
        clock=iter(range(20))
        recovery=Mock(return_value=True)
        with ExitStack() as stack:
            stack.enter_context(patch.object(module.os,'fork',return_value=12345))
            stack.enter_context(patch.object(module.os,'waitid',return_value=None))
            stack.enter_context(patch.object(module.os,'kill'))
            reaper=stack.enter_context(patch.object(module.os,'waitpid'))
            stack.enter_context(patch.object(module.time,'monotonic',side_effect=lambda:next(clock)))
            stack.enter_context(patch.object(module.time,'sleep'))
            with self.assertRaisesRegex(RuntimeError,'ACCEPTANCE_CHILD_STOP_UNCONFIRMED'):
                run_guardian(lambda:True,recovery,.1,1)
            reaper.assert_not_called()
            recovery.assert_not_called()

    def test_fixed_controller_and_journal_complete_without_live_release(self):
        from hue_admin.acceptance_journal import AcceptanceJournal
        from test_acceptance_recovery import Adapter
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);root.chmod(0o700);(root/'state').mkdir(mode=0o700)
            path=root/'fixture-release';path.write_text('0.2.0-rc.5')
            adapter=Adapter()
            adapter.selected=lambda:path.read_text()
            adapter.healthy=lambda expected:path.read_text()==expected
            adapter.preflight=lambda _:None
            adapter.update=lambda candidate:path.write_text(candidate)
            adapter.rollback=lambda:path.write_text('0.2.0-rc.5')
            adapter.candidate_checks=lambda:True
            journal=AcceptanceJournal(root)
            pid=launch_candidate(adapter,journal,'0.2.0-rc.5','0.2.0-rc.7',str(uuid.uuid4()),timeout=2)
            self.assertEqual(self.wait(pid),0)
            self.assertEqual(journal.load()['phase'],'closed')
            self.assertEqual(path.read_text(),'0.2.0-rc.5')

    def test_killed_controller_is_recovered_by_guardian_automatically(self):
        from hue_admin.acceptance_journal import AcceptanceJournal
        from test_acceptance_recovery import Adapter
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);root.chmod(0o700);(root/'state').mkdir(mode=0o700)
            path=root/'fixture-release';path.write_text('0.2.0-rc.5')
            ready=root/'worker-ready'
            adapter=Adapter()
            adapter.selected=lambda:path.read_text()
            adapter.healthy=lambda expected:path.read_text()==expected
            adapter.preflight=lambda _:None
            adapter.update=lambda candidate:path.write_text(candidate)
            adapter.rollback=lambda:path.write_text('0.2.0-rc.5')
            def checks():ready.touch();time.sleep(20);return True
            adapter.candidate_checks=checks
            journal=AcceptanceJournal(root)
            pid=launch_candidate(adapter,journal,'0.2.0-rc.5','0.2.0-rc.7',str(uuid.uuid4()),timeout=25)
            try:
                deadline=time.monotonic()+5
                while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(ready.exists())
                controller=journal.load()['supervisorPid']
                fields=Path(f'/proc/{controller}/stat').read_text().rsplit(') ',1)[1].split()
                self.assertEqual(int(fields[1]),pid)  # Only the fixture guardian's current direct child.
                os.kill(controller,signal.SIGKILL)
                self.assertEqual(self.wait(pid),1)
                pid=None
                result=journal.load()
                self.assertEqual(result['phase'],'closed')
                self.assertEqual(result['outcome'],'interrupted')
                self.assertTrue(result['restoreVerified'])
                self.assertEqual(path.read_text(),'0.2.0-rc.5')
            finally:
                if pid is not None:
                    os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)


if __name__=='__main__':unittest.main()
