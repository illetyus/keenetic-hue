import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hue_admin.backup import seal
from hue_admin.restore import open_backup, stage_backup


class BackupRestoreTests(unittest.TestCase):
    identity = {'adapter': 'F0:F1:F2:F3:F4:F5', 'lamp': 'C0:00:00:00:00:01'}

    def archive(self):
        return seal({'secrets/bond.json': b'private-bond'}, 'fixture-test-password', self.identity)

    def test_roundtrip_and_randomness(self):
        first, second = self.archive(), self.archive()
        self.assertNotEqual(first, second)
        self.assertEqual(open_backup(first, 'fixture-test-password', self.identity),
                         {'secrets/bond.json': b'private-bond'})

    def test_wrong_password_and_tamper_leave_target_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'installed'
            target.mkdir()
            sentinel = target / 'sentinel'
            sentinel.write_bytes(b'unchanged')
            raw = self.archive()
            for data, password in ((raw, 'wrong'), (raw[:-1] + bytes([raw[-1] ^ 1]), 'fixture-test-password')):
                with self.assertRaises(ValueError):
                    stage_backup(data, password, self.identity, target)
                self.assertEqual(list(target.iterdir()), [sentinel])
                self.assertEqual(sentinel.read_bytes(), b'unchanged')

    def test_untrusted_kdf_and_version_rejected_before_derivation(self):
        raw = self.archive()
        header, cipher = raw.split(b'\n', 1)
        for key, value in (('n', 2**30), ('formatVersion', 999)):
            modified = json.loads(header)
            modified[key] = value
            with patch('hue_admin.restore.derive_key') as derive:
                with self.assertRaises(ValueError):
                    open_backup(json.dumps(modified).encode()+b'\n'+cipher, 'fixture-test-password', self.identity)
                derive.assert_not_called()

    def test_paths_and_identity_are_bounded(self):
        for name in ('../escape', '/absolute', 'secrets/../../escape', 'secrets\\escape', 'secrets/bond.json:ads'):
            with self.assertRaises(ValueError):
                seal({name:b'x'}, 'fixture-test-password', self.identity)
        with self.assertRaises(ValueError):
            open_backup(self.archive(), 'fixture-test-password', {'adapter':'different','lamp':'different'})

    def test_private_staging_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'stage'
            stage_backup(self.archive(), 'fixture-test-password', self.identity, target)
            self.assertEqual((target/'secrets/bond.json').read_bytes(), b'private-bond')
            with self.assertRaises(ValueError):
                stage_backup(self.archive(), 'fixture-test-password', self.identity, target)

    def test_disk_failure_removes_only_owned_stage(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'stage'
            with patch('hue_admin.restore.write_private', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    stage_backup(self.archive(), 'fixture-test-password', self.identity, target)
            self.assertFalse(target.exists())

    def test_memory_guard_runs_before_key_derivation(self):
        raw = self.archive()
        with patch('hue_admin.restore.require_memory', side_effect=ValueError('BACKUP_MEMORY')), patch('hue_admin.restore.derive_key') as derive:
            with self.assertRaises(ValueError):
                open_backup(raw, 'fixture-test-password', self.identity)
            derive.assert_not_called()

    @unittest.skipUnless(hasattr(__import__('os'), 'symlink'), 'Symlink unavailable')
    def test_staging_parent_symlink_refused(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            real = Path(root)/'real'
            real.mkdir()
            link = Path(root)/'link'
            try:
                os.symlink(real, link, target_is_directory=True)
            except OSError:
                self.skipTest('Symlink privilege unavailable; native test required')
            with self.assertRaises(ValueError):
                stage_backup(self.archive(), 'fixture-test-password', self.identity, link/'stage')
            self.assertEqual(list(real.iterdir()), [])
