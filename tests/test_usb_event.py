from pathlib import Path
import unittest
from unittest.mock import patch


class UsbEventTests(unittest.TestCase):
    def test_internal_cli_accepts_no_event_supplied_path(self):
        from hue_admin.cli import main
        from hue_admin.service_manager import SYSTEM
        with patch('hue_admin.usb_permissions.refresh_usb_permissions',return_value={'granted':False}) as refresh:
            self.assertEqual(main(['usb-event']),0)
            refresh.assert_called_once_with(SYSTEM)
            with self.assertRaises(SystemExit):main(['usb-event','/dev/attacker'])

    def test_only_selected_live_owner_can_refresh_one_pinned_node(self):
        from hue_admin.usb_permissions import refresh_usb_permissions
        root=Path('/fixture/system');record={'releaseId':'0.2.0-rc.6'}
        with patch('os.geteuid',return_value=0,create=True), \
             patch('hue_admin.service_manager.selection',return_value='0.2.0-rc.6'), \
             patch('hue_admin.runtime_policy.release_policy',return_value={'agent':'dedicated'}), \
             patch('hue_admin.service_manager.owner_record',return_value=record), \
             patch('hue_admin.service_manager.supervisor_owned',return_value=True) as owner, \
             patch('hue_admin.usb_permissions.prepare_usb_permissions',return_value={'granted':True}) as prepare:
            self.assertEqual(refresh_usb_permissions(root),{'granted':True})
            prepare.assert_called_once()
            self.assertEqual(prepare.call_args.args,(root,'0.2.0-rc.6'))
            guard=prepare.call_args.kwargs['running_guard']
            self.assertTrue(guard())
            owner.return_value=False
            self.assertFalse(guard())

    def test_legacy_or_stopped_owner_never_grants_permissions(self):
        from hue_admin.usb_permissions import refresh_usb_permissions
        for policy,record in (({'agent':'root'},None),({'agent':'dedicated'},None)):
            with patch('os.geteuid',return_value=0,create=True), \
                 patch('hue_admin.service_manager.selection',return_value='0.2.0-rc.5'), \
                 patch('hue_admin.runtime_policy.release_policy',return_value=policy), \
                 patch('hue_admin.service_manager.owner_record',return_value=record), \
                 patch('hue_admin.usb_permissions.prepare_usb_permissions') as prepare:
                self.assertFalse(refresh_usb_permissions(Path('/fixture/system'))['granted'])
                prepare.assert_not_called()
