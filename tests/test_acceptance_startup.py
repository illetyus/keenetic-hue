from contextlib import ExitStack
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from hue_admin.acceptance_startup import recover_before_start, physical_snapshot


class StartupTests(unittest.TestCase):
    def test_bootstrap_recovers_before_selecting_manager_for_start(self):
        from hue_admin import bootstrap
        events=[]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'active.json').touch()
            with patch('hue_admin.acceptance_startup.recover_before_start',side_effect=lambda _:events.append('recover')), \
                 patch.object(bootstrap,'selected_manager',side_effect=lambda _:events.append('select') or root), \
                 patch.object(bootstrap.importlib,'import_module',return_value=Mock(main=lambda _:0)):
                self.assertEqual(bootstrap.run(root,['start']),0)
        self.assertEqual(events,['recover','select'])

    def test_bootstrap_skips_startup_recovery_for_help_flags(self):
        from hue_admin import bootstrap
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'active.json').touch()
            for argv in (['start','-h'],['install','--help'],['internal-service','start','-h'],[]):
                with self.subTest(argv=argv), \
                     patch('hue_admin.acceptance_startup.recover_before_start') as recover, \
                     patch.object(bootstrap,'selected_manager',return_value=root), \
                     patch.object(bootstrap.importlib,'import_module',return_value=Mock(main=lambda _:0)):
                    self.assertEqual(bootstrap.run(root,argv),0)
                    recover.assert_not_called()
            with patch('hue_admin.acceptance_startup.recover_before_start') as recover, \
                 patch.object(bootstrap,'selected_manager',return_value=root), \
                 patch.object(bootstrap.importlib,'import_module',return_value=Mock(main=lambda _:0)):
                self.assertEqual(bootstrap.run(root,['start','--','-h']),0)
                recover.assert_called_once_with(root)

    def test_pending_journal_prevents_already_managed_start_shortcut(self):
        from hue_admin.admission import already_managed
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root=Path(directory);(root/'state').mkdir()
            path=root/'state/acceptance-current.json'
            module='hue_admin.admission.manager.'
            stack.enter_context(patch(module+'selection',return_value='0.2.0-rc.5'))
            stack.enter_context(patch(module+'owner_record',return_value={'releaseId':'0.2.0-rc.5'}))
            stack.enter_context(patch(module+'supervisor_owned',return_value=True))
            path.write_text(json.dumps({'snapshot':{'phase':'running','restoreVerified':False}}));path.chmod(0o600)
            self.assertFalse(already_managed(root,['start']))
            path.write_text(json.dumps({'snapshot':{'phase':'closed','restoreVerified':True}}))
            self.assertTrue(already_managed(root,['start']))

    def test_absent_new_journal_does_not_touch_old_rc6_windows_or_services(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'state/acceptance').mkdir(parents=True)
            old=root/'state/acceptance/rc6-test-window.json'
            old.write_text('{"phase":"open"}')
            with patch('hue_admin.acceptance_startup.AcceptanceJournal') as journal:
                self.assertIsNone(recover_before_start(root))
                journal.assert_not_called()
            self.assertEqual(old.read_text(),'{"phase":"open"}')

    def test_pending_recovery_uses_real_journal_and_trusted_fixed_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'state').mkdir();(root/'state/acceptance-current.json').touch()
            with ExitStack() as stack:
                journal=stack.enter_context(patch('hue_admin.acceptance_startup.AcceptanceJournal')).return_value
                journal.load.return_value={'phase':'running'}
                recover=stack.enter_context(patch('hue_admin.acceptance_startup.recover_interrupted',
                                                 return_value={'phase':'closed','restoreVerified':True}))
                result=recover_before_start(root)
                self.assertTrue(result['restoreVerified'])
                self.assertIs(recover.call_args.args[1],journal)
                self.assertEqual(recover.call_args.args[2],journal.quiescent)

    def test_unverified_recovery_cannot_allow_boot_to_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'state').mkdir();(root/'state/acceptance-current.json').touch()
            with patch('hue_admin.acceptance_startup.AcceptanceJournal') as journal, \
                 patch('hue_admin.acceptance_startup.recover_interrupted',return_value={'restoreVerified':False}):
                journal.return_value.load.return_value={'phase':'running'}
                with self.assertRaisesRegex(RuntimeError,'ACCEPTANCE_RECOVERY_UNVERIFIED'):
                    recover_before_start(root)

    def test_physical_snapshot_requires_owned_agent_and_verified_values(self):
        with ExitStack() as stack:
            manager='hue_admin.acceptance_startup.manager.'
            stack.enter_context(patch(manager+'selection',return_value='0.2.0-rc.5'))
            stack.enter_context(patch(manager+'owner_record',return_value={'releaseId':'0.2.0-rc.5'}))
            stack.enter_context(patch(manager+'child_pid',return_value=123))
            owned=stack.enter_context(patch(manager+'child_owned',return_value=True))
            stack.enter_context(patch(manager+'supervisor_owned',return_value=True))
            stack.enter_context(patch('hue_admin.runtime_policy.release_policy',return_value={'agent':'root'}))
            probe=stack.enter_context(patch('hue_admin.acceptance_startup.authenticated_probe',
                 return_value=(200,{'status':'verified','on':False,'brightnessRaw':50,'sessionId':'ignored'})))
            self.assertEqual(physical_snapshot(Path('/fixture')),{'on':False,'brightnessRaw':50})
            owned.return_value=False
            probe.reset_mock()
            with self.assertRaises(ValueError):physical_snapshot(Path('/fixture'))
            probe.assert_not_called()
            owned.return_value=True
            for result in ((503,{'status':'offline'}),(200,{'status':'verified','on':1,'brightnessRaw':50}),
                           (200,{'status':'verified','on':True,'brightnessRaw':255})):
                probe.return_value=result
                with self.assertRaises(ValueError):physical_snapshot(Path('/fixture'))


if __name__=='__main__':unittest.main()
