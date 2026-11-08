import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hue_admin.account_store import _ensure_accounts
from hue_admin.service_accounts import TABLES


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Native root-owned disposable account fixture')
class AccountStoreTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory) / 'system'
        root.mkdir(mode=0o700)
        (root / 'state').mkdir(mode=0o700)
        paths = {}
        for name in TABLES:
            path = Path(directory) / name
            path.write_text('root:*:0:0:root:/root:/bin/sh\n' if name.endswith('passwd')
                            else 'root:*:0:\n')
            path.chmod(0o600)
            paths[name] = path
        return root, paths

    def test_persistent_identity_and_reboot_account_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root, paths = self.fixture(directory)
            original = {k: p.read_text() for k, p in paths.items()}
            profiles = _ensure_accounts(root, paths)
            self.assertEqual(profiles, _ensure_accounts(root, paths))
            paths['native_passwd'].write_text(original['native_passwd'])
            paths['native_group'].write_text(original['native_group'])
            self.assertEqual(profiles, _ensure_accounts(root, paths))
            for path in paths.values():
                self.assertEqual(path.read_text().count('hue-tunnel:'), 1)
            profile = root / 'state/service-identities.json'
            self.assertEqual(json.loads(profile.read_text()), profiles)
            self.assertEqual(profile.stat().st_mode & 0o777, 0o600)

    def test_world_writable_database_is_rejected_before_profile_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root, paths = self.fixture(directory)
            paths['native_group'].chmod(0o666)
            with self.assertRaises(ValueError):
                _ensure_accounts(root, paths)
            self.assertFalse((root / 'state/service-identities.json').exists())

    def test_unexpected_database_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, paths = self.fixture(directory)
            target = paths['native_passwd']
            link = Path(directory) / 'symlink'
            link.symlink_to(target)
            paths['native_passwd'] = link
            with self.assertRaises(ValueError):
                _ensure_accounts(root, paths)
            self.assertNotIn('hue-tunnel:', target.read_text())

    def interrupt_append(self, paths):
        original_write = os.write
        target = paths['entware_passwd']
        def write(fd, data):
            if Path('/proc/self/fd/' + str(fd)).resolve() == target:
                original_write(fd, data[:13])
                raise InterruptedError('fixture: interrupted after a partial row')
            return original_write(fd, data)
        return patch('hue_admin.account_store.os.write', side_effect=write)

    def test_torn_append_is_completed_from_durable_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root, paths = self.fixture(directory)
            with self.interrupt_append(paths), self.assertRaises(InterruptedError):
                _ensure_accounts(root, paths)
            self.assertTrue((root / 'state/account-append.json').is_file())
            profiles = json.loads((root / 'state/service-identities.json').read_text())
            self.assertEqual(_ensure_accounts(root, paths), profiles)
            self.assertFalse((root / 'state/account-append.json').exists())
            for path in paths.values():
                self.assertEqual(path.read_text().count('hue-tunnel:'), 1)
                self.assertEqual(path.read_text().count('hue-agent:'), 1)

    def test_unrelated_change_after_torn_append_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root, paths = self.fixture(directory)
            with self.interrupt_append(paths), self.assertRaises(InterruptedError):
                _ensure_accounts(root, paths)
            with paths['entware_passwd'].open('a') as output:
                output.write('unrelated-change\n')
            before = {k: p.read_bytes() for k, p in paths.items()}
            with self.assertRaises(ValueError):
                _ensure_accounts(root, paths)
            self.assertEqual(before, {k: p.read_bytes() for k, p in paths.items()})

    def test_complete_append_before_receipt_removal_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root, paths = self.fixture(directory)
            original = Path.unlink
            def unlink(path, *args, **kwargs):
                if path.name == 'account-append.json':
                    raise InterruptedError('fixture: before receipt removal')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'unlink', unlink), self.assertRaises(InterruptedError):
                _ensure_accounts(root, paths)
            _ensure_accounts(root, paths)
            for path in paths.values():
                self.assertEqual(path.read_text().count('hue-tunnel:'), 1)

    def test_recovery_rejects_arbitrary_account_rows_in_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root, paths = self.fixture(directory)
            with self.interrupt_append(paths), self.assertRaises(InterruptedError):
                _ensure_accounts(root, paths)
            pending = root / 'state/account-append.json'
            receipt = json.loads(pending.read_text())
            receipt['suffix'] = 'backdoor:*:0:0::/:/bin/sh\n'
            pending.write_text(json.dumps(receipt))
            before = {k: p.read_bytes() for k, p in paths.items()}
            with self.assertRaises(ValueError):
                _ensure_accounts(root, paths)
            self.assertEqual(before, {k: p.read_bytes() for k, p in paths.items()})

    def test_noncooperating_append_is_preserved_and_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, paths = self.fixture(directory)
            original_write = os.write
            target = paths['entware_passwd']
            other = 'other:*:62000:62000:other:/nonexistent:/bin/false\n'
            added = False
            def write(fd, data):
                nonlocal added
                if not added and Path('/proc/self/fd/' + str(fd)).resolve() == target:
                    added = True
                    # Simulate a firmware writer that does not honor flock.
                    with target.open('ab') as output:
                        output.write(other.encode())
                return original_write(fd, data)
            with patch('hue_admin.account_store.os.write', side_effect=write):
                with self.assertRaises(ValueError):
                    _ensure_accounts(root, paths)
            self.assertIn(other, target.read_text())
