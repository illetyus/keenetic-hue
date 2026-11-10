import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hue_admin import lifecycle_events as events
from hue_admin import service_manager as manager
from hue_admin.cli import main, lifecycle_operations


class SafeLifecycleTests(unittest.TestCase):
    def test_only_known_error_codes_are_exposed(self):
        self.assertEqual(events.safe_reason(RuntimeError('AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA')),
                         'AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA')
        self.assertEqual(events.safe_reason(ValueError('AGENT_RECOVERY_CHANGED')),'AGENT_RECOVERY_CHANGED')
        for value in ('secret-token-value','AGENT_RECOVERY_CHANGED secret-token-value','AGENT_RECOVERY_TOKEN'):
            self.assertNotIn(value,events.safe_reason(RuntimeError(value)))

    def test_cli_error_reports_safe_recovery_stage_without_exception_text(self):
        with patch('hue_admin.cli.is_unified',return_value=True), \
             patch('hue_admin.cli.lifecycle_guard'), \
             patch('hue_admin.release_commands.recover_locked',side_effect=RuntimeError('secret-token-value')), \
             patch('sys.stderr',new_callable=io.StringIO) as out:
            self.assertEqual(main(['start']),1)
            self.assertIn('stage=lifecycle_recovery',out.getvalue())
            self.assertNotIn('secret-token-value',out.getvalue())

    def test_tunnel_dependency_failure_is_recorded_without_starting_it(self):
        with patch('hue_admin.cli.is_unified',return_value=True), \
             patch('hue_admin.cli.run_service',return_value=False) as run, \
             patch('hue_admin.lifecycle_events.emit') as emit:
            self.assertEqual(lifecycle_operations('start',None),1)
            run.assert_called_once_with('agent','start')
            self.assertTrue(any(call.args[1:3]==('tunnel','dependency_blocked') for call in emit.call_args_list))

    def test_emit_and_last_failure_never_raise_attribute_error_on_missing_onofollow(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # On platforms where os.O_NOFOLLOW is absent (like Windows), emit returns False and last_failure returns None
            self.assertFalse(events.emit(root, 'agent', 'operation_failed', 'checkpoint', 'TEST_FAIL'))
            self.assertIsNone(events.last_failure(root, 'agent'))


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'Native root log boundary')
class LifecycleStorageTests(unittest.TestCase):
    def fixture(self,directory):
        root=Path(directory)
        for name in ('run','logs'):(root/name).mkdir(mode=0o700)
        return root

    def test_records_stage_identity_and_bounded_rotation_without_sensitive_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            self.assertTrue(events.emit(root,'agent','operation_failed','checkpoint','AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA'))
            value=events.last_failure(root,'agent')
            self.assertEqual(value['stage'],'checkpoint');self.assertEqual(value['reason'],'AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA')
            for key in ('bootId','pid','startTicks','utc','monotonic'):self.assertIn(key,value)
            log=root/'logs/lifecycle.log'
            log.write_text(' '*(events.LOG_LIMIT-20));log.chmod(0o600)
            self.assertTrue(events.emit(root,'agent','operation_succeeded','startup','OK'))
            self.assertLessEqual(log.stat().st_size,events.LOG_LIMIT)
            self.assertLessEqual(log.with_suffix('.log.previous').stat().st_size,events.LOG_LIMIT)
            self.assertIsNone(events.last_failure(root,'agent'))

    def test_unsafe_log_path_cannot_change_a_target_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory);victim=root/'victim';victim.write_text('unchanged')
            (root/'logs/lifecycle.log').symlink_to(victim)
            self.assertFalse(events.emit(root,'agent','operation_failed','checkpoint','OS_ERROR'))
            self.assertEqual(victim.read_text(),'unchanged')

    def test_checkpoint_exception_is_visible_in_health_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            with patch.object(manager,'selection',return_value='0.2.0'), \
                 patch.object(manager,'owner_record',return_value=None), \
                 patch.object(manager,'supervisor_owned',return_value=False), \
                 patch.object(manager,'stop_orphan',return_value=True), \
                 patch.object(manager,'finalize_stopped_agent',side_effect=RuntimeError('AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA')):
                self.assertFalse(manager.action('agent','start',root))
            self.assertEqual(events.last_failure(root,'agent')['stage'],'checkpoint')
            self.assertEqual(events.unavailable_reason(root,'agent',None,'0.2.0'),
                             'CHECKPOINT:AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA')
            from hue_admin.unified_health import status
            with patch.object(manager,'selection',return_value='0.2.0'), \
                 patch.object(manager,'owner_record',return_value=None), \
                 patch.object(manager,'child_pid',return_value=None):
                value=status(False,root)
            self.assertEqual(value['components']['agent']['reason'],'CHECKPOINT:AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA')

    def test_missing_terminal_record_does_not_invent_a_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            record={'pid':999999,'bootId':'00000000-0000-0000-0000-000000000000',
                    'startTicks':'1','releaseId':'0.2.0','schema':1,'ownerFd':8}
            self.assertEqual(events.unavailable_reason(root,'agent',record,'0.2.0'),'SUPERVISOR_EXIT_UNKNOWN')

    def test_new_launch_timeout_is_not_hidden_by_previous_owner_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            old={'pid':999999,'bootId':events._boot(),'startTicks':'1','releaseId':'0.2.0'}
            self.assertTrue(events.emit(root,'agent','supervisor_exit','supervisor','OBSERVED_TERM',owner=old))
            terminal=json.loads((root/'logs/lifecycle.log').read_text().splitlines()[-1]);terminal['signal']='TERM'
            path=root/'run/agent-terminal.json';path.write_text(json.dumps(terminal));path.chmod(0o600)
            attempt={'pid':999998,'startTicks':'2'}
            self.assertTrue(events.emit(root,'agent','operation_failed','owner_wait','OWNER_WAIT_TIMEOUT',release_id='0.2.0',attempt=attempt))
            self.assertEqual(events.unavailable_reason(root,'agent',old,'0.2.0'),'OWNER_WAIT:OWNER_WAIT_TIMEOUT')

    def test_published_timed_out_attempt_without_terminal_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root=self.fixture(directory)
            owner={'pid':999999,'bootId':events._boot(),'startTicks':'1','releaseId':'0.2.0'}
            self.assertTrue(events.emit(root,'agent','operation_failed','owner_wait','OWNER_WAIT_TIMEOUT',release_id='0.2.0',attempt=owner))
            self.assertEqual(events.unavailable_reason(root,'agent',owner,'0.2.0'),'SUPERVISOR_EXIT_UNKNOWN')


if __name__=='__main__':unittest.main()
