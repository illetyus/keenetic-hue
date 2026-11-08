import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hue_admin.archive_commands import backup_to, verify_backup


class BackupCommandsTests(unittest.TestCase):
    identity={'adapter':'fixture-adapter','lamp':'fixture-lamp'}

    def test_backup_file_private_and_existing_file_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private.huebak'
            with patch('hue_admin.archive_commands.capture_current',return_value=({'secrets/bond.json':b'secret'},self.identity)):
                backup_to(path,'fixture-password')
                self.assertEqual(verify_backup(path,'fixture-password',self.identity),{'secrets/bond.json':b'secret'})
                original=path.read_bytes()
                with self.assertRaises((ValueError,FileExistsError)):
                    backup_to(path,'fixture-password')
                self.assertEqual(path.read_bytes(),original)

    def test_failed_write_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'private.huebak'
            with patch('hue_admin.archive_commands.capture_current',return_value=({'secrets/bond.json':b'secret'},self.identity)),patch('hue_admin.archive_commands.os.fsync',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):backup_to(path,'fixture-password')
            self.assertFalse(path.exists())
