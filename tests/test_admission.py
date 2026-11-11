import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hue_admin.admission import admit,already_managed


class AdmissionTests(unittest.TestCase):
    def test_pending_migration_cannot_bypass_recovery_even_when_owners_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'state').mkdir();(root/'state/migration.json').write_text('{"schema":1,"phase":"started"}')
            with patch('hue_admin.admission.manager.selection',return_value='0.2.0'),patch('hue_admin.admission.manager.owner_record',return_value={'releaseId':'0.2.0'}),patch('hue_admin.admission.manager.supervisor_owned',return_value=True):
                self.assertFalse(already_managed(root,['start']))

    def test_old_version_cannot_bypass_verification_as_already_managed(self):
        with patch('hue_admin.admission.manager.selection',return_value='0.2.0'),patch('hue_admin.admission.manager.owner_record',return_value={'releaseId':'0.1.0'}),patch('hue_admin.admission.manager.supervisor_owned',return_value=True):
            self.assertFalse(already_managed(Path('/fixture'),['start','agent']))

    @unittest.skipUnless(os.name=='posix','Native flock')
    def test_maintenance_is_serial_but_verified_owned_start_does_not_load_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('hue_admin.admission.already_managed',return_value=False),admit(root,['backup','file'],timeout=0) as first:
                self.assertTrue(first)
                with self.assertRaises(RuntimeError):
                    with admit(root,['start'],timeout=0):pass
                with patch('hue_admin.admission.already_managed',return_value=True),admit(root,['start'],timeout=0) as second:
                    self.assertFalse(second)
                with admit(root,['health','--live'],timeout=0) as readonly:self.assertTrue(readonly)

    def test_help_flags_bypass_admission_lock(self):
        root = Path('/nonexistent/fixture')
        with admit(root, ['-h'], timeout=0) as allowed_h:
            self.assertTrue(allowed_h)
        with admit(root, ['--help'], timeout=0) as allowed_help:
            self.assertTrue(allowed_help)
        with admit(root, ['install', '-h'], timeout=0) as allowed_subcmd:
            self.assertTrue(allowed_subcmd)

    def test_empty_argv_bypasses_admission_lock_and_double_dash_does_not(self):
        from unittest.mock import MagicMock
        root = Path('/nonexistent/fixture')
        with admit(root, [], timeout=0) as allowed_empty:
            self.assertTrue(allowed_empty)
        with tempfile.TemporaryDirectory() as tmp:
            troot = Path(tmp)
            with patch.dict('sys.modules', {'fcntl': MagicMock()}), \
                 patch('hue_admin.admission.os.open', side_effect=OSError('LOCK_ATTEMPTED')) as opener:
                with self.assertRaisesRegex(OSError, 'LOCK_ATTEMPTED'):
                    with admit(troot, ['backup', '--', '-h'], timeout=0):
                        pass
                opener.assert_called_once()
