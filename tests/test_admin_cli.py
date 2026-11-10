import io
import json
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from hue_admin.cli import main


class AdminCliTests(unittest.TestCase):
    def test_generated_backup_password_is_terminal_only_and_confirmed(self):
        password='fixture-generated-password-1234'
        with patch('hue_admin.cli.sys.stdin.isatty',return_value=True),patch('hue_admin.cli.sys.stderr',new_callable=io.StringIO) as out,patch('hue_admin.cli.getpass',side_effect=['?',password]),patch('secrets.token_urlsafe',return_value=password),patch('hue_admin.cli.archive_action',return_value={'created':True}) as action:
            out.isatty=lambda:True
            self.assertEqual(main(['backup','/private/backup.huebak']),0)
            self.assertIn(password,out.getvalue())
            action.assert_called_once_with('backup','/private/backup.huebak',password,False,False)
    def setUp(self):
        guard=patch('hue_admin.cli.lifecycle_guard',return_value=nullcontext());guard.start();self.addCleanup(guard.stop)
        unified=patch('hue_admin.cli.is_unified',return_value=False);unified.start();self.addCleanup(unified.stop)

    def test_update_dispatches_verified_release_workflow(self):
        with patch('hue_admin.cli.release_action',return_value={'activated':True}) as action:
            self.assertEqual(main(['update','/private/candidate.huerel']),0)
            action.assert_called_once_with('update','/private/candidate.huerel',None,None,None)

    def test_boot_entry_uses_internal_recovery_before_start(self):
        with patch('hue_admin.cli.release_action',return_value={'configured':False}) as action:
            self.assertEqual(main(['internal-service','start']),0)
            action.assert_called_once_with('internal-service',None,None,None,'start')

    def test_backup_never_prompts_or_reads_keys_without_private_terminal(self):
        with patch('hue_admin.cli.sys.stdin.isatty',return_value=False),patch('hue_admin.cli.getpass') as prompt:
            self.assertEqual(main(['backup','/private/backup.huebak']),2)
            prompt.assert_not_called()

    def test_restore_dry_run_requires_password_but_does_not_apply(self):
        with patch('hue_admin.cli.sys.stdin.isatty',return_value=True),patch('hue_admin.cli.sys.stderr.isatty',return_value=True),patch('hue_admin.cli.getpass',return_value='test-password'),patch('hue_admin.cli.archive_action',return_value={'verified':True}) as action:
            self.assertEqual(main(['restore','/private/backup.huebak','--dry-run']),0)
            action.assert_called_once_with('restore','/private/backup.huebak','test-password',True,False)

    def test_setup_code_requires_terminal_and_never_requests_when_redirected(self):
        with patch('hue_admin.cli.sys.stdout.isatty',return_value=False),patch('hue_admin.cli.issue_code') as issue:
            self.assertEqual(main(['setup-code']),2)
            issue.assert_not_called()
        with patch('hue_admin.cli.sys.stdout.isatty',return_value=True),patch('hue_admin.cli.sys.stdin.isatty',return_value=False),patch('hue_admin.cli.issue_code') as issue:
            self.assertEqual(main(['setup-code']),2)
            issue.assert_not_called()

    def test_setup_code_is_shown_only_after_explicit_terminal_command(self):
        with patch('hue_admin.cli.sys.stdin.isatty',return_value=True),patch('hue_admin.cli.sys.stdout',new_callable=io.StringIO) as out,patch('hue_admin.cli.issue_code',return_value={'code':'A'*26,'expiresIn':600}):
            out.isatty=lambda:True
            self.assertEqual(main(['setup-code']),0)
            self.assertIn('A'*26,out.getvalue());self.assertIn('600',out.getvalue())

    def test_backup_handles_keyboard_interrupt_without_traceback(self):
        with patch('hue_admin.cli.sys.stdin.isatty',return_value=True),patch('hue_admin.cli.sys.stderr',new_callable=io.StringIO) as err,patch('hue_admin.cli.getpass',side_effect=KeyboardInterrupt):
            err.isatty=lambda:True
            self.assertEqual(main(['backup','/private/backup.huebak']),1)
            self.assertIn('BACKUP_OR_RESTORE_FAILED',err.getvalue())
    def test_unknown_target_never_runs_a_service(self):
        with patch('hue_admin.cli.run_service') as run:
            with self.assertRaises(SystemExit):
                main(['restart', 'python3; reboot'])
            run.assert_not_called()

    def test_restart_tunnel_does_not_touch_agent(self):
        with patch('hue_admin.cli.run_service', return_value=True) as run:
            self.assertEqual(main(['restart', 'tunnel']), 0)
            run.assert_called_once_with('tunnel', 'restart')

    def test_json_status_has_schema_without_raw_process_output(self):
        value={'schemaVersion':1,'healthy':False,'components':{'agent':{'state':'stopped','observedAt':123,'reason':'PROCESS_ABSENT'}}}
        with patch('hue_admin.cli.status', return_value=value),patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(['status','--json']),1)
            self.assertEqual(json.loads(out.getvalue()),value)

    def test_stop_order_is_reverse_start_order(self):
        with patch('hue_admin.cli.run_service',return_value=True) as run:
            self.assertEqual(main(['stop']),0)
            self.assertEqual([c.args for c in run.call_args_list],[('tunnel','stop'),('agent','stop')])

    def test_failed_tunnel_stop_still_attempts_agent_stop(self):
        for action in ('stop','restart'):
            with patch('hue_admin.cli.run_service',side_effect=[False,True]) as run, \
                 patch('hue_admin.cli.service_failure_details') as details:
                self.assertEqual(main([action]),1)
                self.assertEqual([c.args for c in run.call_args_list],[('tunnel','stop'),('agent','stop')])
                details.assert_called_once_with(None)
