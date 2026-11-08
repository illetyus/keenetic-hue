from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hue_admin import service_manager as manager


class AgentHandoffTests(unittest.TestCase):
    def test_stop_exports_current_state_before_restoring_usb_and_removing_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'run').mkdir();record={'releaseId':'0.2.0-rc.6'};events=[]
            path=root/'run/agent-supervisor.json';path.write_text('fixture')
            def checkpoint(*args):
                self.assertTrue(path.exists());events.append('checkpoint')
            def usb(*args):
                self.assertTrue(path.exists());events.append('usb')
            with patch.object(manager,'owner_record',return_value=record), \
                 patch.object(manager,'supervisor_owned',return_value=False), \
                 patch.object(manager,'file_lock',return_value=nullcontext(9)), \
                 patch.object(manager,'stop_orphan',return_value=True), \
                 patch('hue_admin.runtime_policy.release_policy',return_value={'agent':'dedicated'}), \
                 patch('hue_admin.agent_runtime.checkpoint_agent_state',side_effect=checkpoint), \
                 patch('hue_admin.usb_permissions.restore_usb_permissions',side_effect=usb):
                self.assertTrue(manager.stop_locked('agent',root))
            self.assertEqual(events,['checkpoint','usb']);self.assertFalse(path.exists())

    def test_failed_checkpoint_keeps_recovery_owner_and_does_not_change_usb(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'run').mkdir();record={'releaseId':'0.2.0-rc.6'}
            path=root/'run/agent-supervisor.json';path.write_text('fixture')
            with patch.object(manager,'owner_record',return_value=record), \
                 patch.object(manager,'supervisor_owned',return_value=False), \
                 patch.object(manager,'file_lock',return_value=nullcontext(9)), \
                 patch.object(manager,'stop_orphan',return_value=True), \
                 patch('hue_admin.runtime_policy.release_policy',return_value={'agent':'dedicated'}), \
                 patch('hue_admin.agent_runtime.checkpoint_agent_state',side_effect=ValueError('fixture failure')), \
                 patch('hue_admin.usb_permissions.restore_usb_permissions') as usb:
                with self.assertRaises(ValueError):manager.stop_locked('agent',root)
                usb.assert_not_called()
            self.assertTrue(path.exists())
