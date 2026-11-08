import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'POSIX root boundary')
class KernelControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'state').mkdir(mode=0o700)
        self.sysctl = self.root / 'fake-sysctl'
        for name in ('fs', 'kernel'):
            (self.sysctl / name).mkdir(parents=True)
        for name in ('fs/protected_hardlinks', 'fs/protected_symlinks',
                     'kernel/dmesg_restrict', 'kernel/kptr_restrict'):
            (self.sysctl / name).write_text('0\n')
        (self.sysctl / 'kernel/randomize_va_space').write_text('2\n')

    def test_existing_unrestored_receipt_blocks_new_transaction(self):
        from hue_admin.kernel_controller import apply_policy, restore_policy
        result = apply_policy(self.root, self.sysctl, lambda: True)
        self.assertEqual(result['phase'], 'applied')
        with self.assertRaisesRegex(ValueError, 'KERNEL_RECOVERY_REQUIRED'):
            apply_policy(self.root, self.sysctl, lambda: True)
        self.assertEqual(restore_policy(self.root, self.sysctl)['phase'], 'restored')
        self.assertEqual((self.sysctl / 'fs/protected_hardlinks').read_text(), '0\n')
        self.assertEqual(apply_policy(self.root, self.sysctl, lambda: True)['phase'], 'applied')

    def test_failed_acceptance_restores_values_and_durable_receipt(self):
        from hue_admin.kernel_controller import apply_policy
        from hue_admin.kernel_receipt import KernelReceipt
        with self.assertRaisesRegex(ValueError, 'KERNEL_ACCEPTANCE'):
            apply_policy(self.root, self.sysctl, lambda: False)
        self.assertEqual((self.sysctl / 'kernel/dmesg_restrict').read_text(), '0\n')
        self.assertEqual(KernelReceipt(self.root).load()['phase'], 'restored')

    def test_interrupted_write_requires_explicit_recovery(self):
        from hue_admin.kernel_controller import apply_policy, restore_policy
        from hue_admin.kernel_receipt import KernelReceipt
        from hue_admin.kernel_sysctl import SysctlFiles
        from hue_admin.kernel_transaction import apply_kernel
        receipt = KernelReceipt(self.root)
        with SysctlFiles(self.sysctl) as controls:
            def interrupted(name, value):
                controls.write(name, value)
                raise SystemExit('simulated process loss')
            with self.assertRaises(SystemExit):
                apply_kernel(controls.read, interrupted, receipt.save, lambda: True)
        before = receipt.load()
        self.assertEqual(before['phase'], 'applying')
        with self.assertRaisesRegex(ValueError, 'KERNEL_RECOVERY_REQUIRED'):
            apply_policy(self.root, self.sysctl, lambda: True)
        self.assertEqual(receipt.load(), before)
        self.assertEqual(restore_policy(self.root, self.sysctl)['phase'], 'restored')
        self.assertEqual((self.sysctl / 'fs/protected_hardlinks').read_text(), '0\n')

    def test_previous_boot_can_be_reconciled_only_when_baseline_matches(self):
        from hue_admin.kernel_controller import apply_policy, reconcile_policy
        from hue_admin.kernel_receipt import KernelReceipt
        first = '11111111-1111-1111-1111-111111111111'
        second = '22222222-2222-2222-2222-222222222222'
        with patch('hue_admin.kernel_receipt._boot_id', return_value=first):
            apply_policy(self.root, self.sysctl, lambda: True)
        with patch('hue_admin.kernel_receipt._boot_id', return_value=second):
            with self.assertRaisesRegex(ValueError, 'KERNEL_BOOT_BASELINE_CHANGED'):
                reconcile_policy(self.root, self.sysctl)
            for name in ('fs/protected_hardlinks', 'fs/protected_symlinks',
                         'kernel/dmesg_restrict', 'kernel/kptr_restrict'):
                (self.sysctl / name).write_text('0\n')
            self.assertEqual(reconcile_policy(self.root, self.sysctl)['phase'], 'restored')
            self.assertEqual(KernelReceipt(self.root).load()['phase'], 'restored')

    def test_ensure_is_idempotent_and_reapplies_after_matching_boot_reset(self):
        from hue_admin.kernel_controller import ensure_policy
        from hue_admin.kernel_receipt import KernelReceipt
        first = '11111111-1111-1111-1111-111111111111'
        second = '22222222-2222-2222-2222-222222222222'
        with patch('hue_admin.kernel_receipt._boot_id', return_value=first):
            self.assertEqual(ensure_policy(self.root, self.sysctl, lambda: True)['phase'], 'applied')
            original = KernelReceipt(self.root).load()
            self.assertEqual(ensure_policy(self.root, self.sysctl, lambda: False), original)
        for name in ('fs/protected_hardlinks', 'fs/protected_symlinks',
                     'kernel/dmesg_restrict', 'kernel/kptr_restrict'):
            (self.sysctl / name).write_text('0\n')
        with patch('hue_admin.kernel_receipt._boot_id', return_value=second):
            self.assertEqual(ensure_policy(self.root, self.sysctl, lambda: True)['phase'], 'applied')
            self.assertEqual((self.sysctl / 'kernel/kptr_restrict').read_text(), '1\n')

    def test_ensure_refuses_external_change_to_applied_state(self):
        from hue_admin.kernel_controller import ensure_policy
        ensure_policy(self.root, self.sysctl, lambda: True)
        (self.sysctl / 'kernel/kptr_restrict').write_text('2\n')
        with self.assertRaisesRegex(ValueError, 'KERNEL_APPLIED_STATE_CHANGED'):
            ensure_policy(self.root, self.sysctl, lambda: True)
        self.assertEqual((self.sysctl / 'kernel/kptr_restrict').read_text(), '2\n')

    def test_ensure_does_not_clear_same_boot_rollback_conflict(self):
        from hue_admin.kernel_controller import ensure_policy
        from hue_admin.kernel_receipt import KernelReceipt
        receipt = {'schema': 1, 'phase': 'rollback_conflict',
                   'attempted': ['fs.protected_hardlinks'], 'observed': {
                       'fs.protected_hardlinks': 0, 'fs.protected_symlinks': 0,
                       'kernel.dmesg_restrict': 0, 'kernel.kptr_restrict': 0,
                       'kernel.randomize_va_space': 2}}
        KernelReceipt(self.root).save(receipt)
        with self.assertRaisesRegex(ValueError, 'KERNEL_RECOVERY_REQUIRED'):
            ensure_policy(self.root, self.sysctl, lambda: True)
        self.assertEqual(KernelReceipt(self.root).load(), receipt)
        self.assertEqual((self.sysctl / 'fs/protected_hardlinks').read_text(), '0\n')
