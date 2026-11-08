import tempfile
import unittest
from pathlib import Path
from hue_admin.health import process_state, status
from hue_admin.inventory import redact


class AdminHealthTests(unittest.TestCase):
    def test_pid_is_not_enough_wrong_command_is_not_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'pid').write_text('123')
            (root/'proc/123').mkdir(parents=True);(root/'proc/123/cmdline').write_bytes(b'python3\0unrelated.py\0')
            self.assertEqual(process_state(root/'pid',b'hue_agent',root/'proc'),'PROCESS_MISMATCH')

    def test_pid_path_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'pid').write_text('../bad')
            self.assertEqual(process_state(root/'pid',b'hue_agent',root/'proc'),'INVALID_PID')

    def test_secret_fields_redacted_recursively(self):
        result=redact({'token':'abc','component':{'password':'bad','ok':True},'keystore':'secret'})
        self.assertNotIn('abc',str(result));self.assertNotIn('bad',str(result));self.assertNotIn('secret',str(result))
        self.assertTrue(result['component']['ok'])

    def test_status_without_live_never_opens_network(self):
        from unittest.mock import patch
        with patch('hue_admin.health.probe') as probe:
            value=status(False)
            probe.assert_not_called();self.assertEqual(value['schemaVersion'],1)
