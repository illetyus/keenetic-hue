import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hue_admin.backup import seal, derive_key
from hue_admin.archive_commands import backup_to

class PasswordPolicyTests(unittest.TestCase):
    identity={'adapter':'fixture-adapter','lamp':'fixture-lamp'}

    def test_short_and_repeated_new_password_rejected_before_derivation(self):
        for password in ('x','a'*20,'short password'):
            with self.subTest(password_length=len(password)), patch('hue_admin.backup.derive_key',return_value=b'0'*32) as derive:
                with self.assertRaisesRegex(ValueError,'BACKUP_PASSWORD'):
                    seal({'secrets/bond.json':b'dummy'},password,self.identity)
                derive.assert_not_called()

    def test_bad_password_rejected_before_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp, patch('hue_admin.archive_commands.capture_current',return_value=({'secrets/bond.json':b'dummy'},self.identity)) as capture:
            with self.assertRaisesRegex(ValueError,'BACKUP_PASSWORD'):
                backup_to(Path(tmp)/'bad.huebak','x')
            capture.assert_not_called()

    def test_existing_password_derivation_unchanged(self):
        self.assertEqual(len(derive_key('x',b'0'*16)),32)
        from hue_admin.restore import open_backup
        with patch('hue_admin.backup.validate_new_backup_password'):
            legacy=seal({'secrets/bond.json':b'dummy'},'x',self.identity)
        self.assertEqual(open_backup(legacy,'x',self.identity),{'secrets/bond.json':b'dummy'})

    def test_unicode_policy_counts_characters_not_bytes(self):
        with self.assertRaisesRegex(ValueError,'BACKUP_PASSWORD'):
            seal({'secrets/bond.json':b'dummy'},'şğ'*4,self.identity)
