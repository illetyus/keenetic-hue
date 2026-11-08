import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'POSIX root receipt boundary')
class KernelReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'state').mkdir(mode=0o700)
        self.value = {'schema': 1, 'phase': 'prepared', 'attempted': [], 'observed': {
            'fs.protected_hardlinks': 0, 'fs.protected_symlinks': 0,
            'kernel.dmesg_restrict': 0, 'kernel.kptr_restrict': 0,
            'kernel.randomize_va_space': 2}}

    def test_roundtrip_private_and_reject_invalid_receipt(self):
        from hue_admin.kernel_receipt import KernelReceipt
        store = KernelReceipt(self.root)
        store.save(self.value)
        self.assertEqual(store.load(), self.value)
        path = self.root / 'state/kernel-transaction.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            store.save({**self.value, 'attempted': ['not.allowed']})
        self.assertEqual(store.load(), self.value)

    def test_bad_mode_and_symlink_are_rejected(self):
        from hue_admin.kernel_receipt import KernelReceipt
        store = KernelReceipt(self.root)
        store.save(self.value)
        path = self.root / 'state/kernel-transaction.json'
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            store.load()
        path.unlink()
        path.symlink_to('/etc/passwd')
        with self.assertRaises((ValueError, OSError)):
            store.load()
        with self.assertRaises((ValueError, OSError)):
            store.save(self.value)

    def test_duplicate_json_keys_are_rejected(self):
        from hue_admin.kernel_receipt import KernelReceipt
        store = KernelReceipt(self.root)
        path = self.root / 'state/kernel-transaction.json'
        path.write_text('{"schema":1,"schema":1}')
        path.chmod(0o600)
        with self.assertRaises(ValueError):
            store.load()

    def test_restrictive_umask_still_produces_readable_private_receipt(self):
        from hue_admin.kernel_receipt import KernelReceipt
        store = KernelReceipt(self.root)
        old = os.umask(0o777)
        try:
            store.save(self.value)
        finally:
            os.umask(old)
        path = self.root / 'state/kernel-transaction.json'
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(store.load(), self.value)

    def test_receipt_from_different_boot_cannot_be_replayed_or_overwritten(self):
        from hue_admin.kernel_receipt import KernelReceipt
        store = KernelReceipt(self.root)
        with patch('hue_admin.kernel_receipt._boot_id', return_value='11111111-1111-1111-1111-111111111111'):
            store.save(self.value)
        with patch('hue_admin.kernel_receipt._boot_id', return_value='22222222-2222-2222-2222-222222222222'):
            with self.assertRaisesRegex(ValueError, 'KERNEL_RECEIPT_BOOT_CHANGED'):
                store.load()
            with self.assertRaisesRegex(ValueError, 'KERNEL_RECEIPT_BOOT_CHANGED'):
                store.save(self.value)
        with patch('hue_admin.kernel_receipt._boot_id', return_value='11111111-1111-1111-1111-111111111111'):
            self.assertEqual(store.load(), self.value)

    def test_restored_receipt_from_previous_boot_also_requires_reconciliation(self):
        from hue_admin.kernel_receipt import KernelReceipt
        store = KernelReceipt(self.root)
        value = {**self.value, 'phase': 'restored'}
        with patch('hue_admin.kernel_receipt._boot_id', return_value='11111111-1111-1111-1111-111111111111'):
            store.save(value)
        with patch('hue_admin.kernel_receipt._boot_id', return_value='22222222-2222-2222-2222-222222222222'):
            with self.assertRaisesRegex(ValueError, 'KERNEL_RECEIPT_BOOT_CHANGED'):
                store.load()

    def test_boot_reconciliation_requires_unchanged_baseline(self):
        from hue_admin.kernel_receipt import KernelReceipt
        store = KernelReceipt(self.root)
        previous = {**self.value, 'phase': 'applying', 'attempted': ['fs.protected_hardlinks']}
        with patch('hue_admin.kernel_receipt._boot_id', return_value='11111111-1111-1111-1111-111111111111'):
            store.save(previous)
        values = dict(self.value['observed'])
        with patch('hue_admin.kernel_receipt._boot_id', return_value='22222222-2222-2222-2222-222222222222'):
            values['fs.protected_hardlinks'] = 1
            with self.assertRaisesRegex(ValueError, 'KERNEL_BOOT_BASELINE_CHANGED'):
                store.reconcile_boot(values.__getitem__)
            values['fs.protected_hardlinks'] = 0
            result = store.reconcile_boot(values.__getitem__)
            self.assertEqual(result['phase'], 'restored')
            self.assertEqual(store.load(), result)
