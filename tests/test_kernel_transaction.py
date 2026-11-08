import copy
import unittest

from hue_admin.kernel_policy import ASLR, CONTROLS


class KernelTransactionTests(unittest.TestCase):
    def setUp(self):
        self.values = dict.fromkeys(CONTROLS, 0)
        self.values[ASLR] = 2
        self.saved = []
        self.writes = []

    def save(self, receipt):
        self.saved.append(copy.deepcopy(receipt))

    def write(self, name, value):
        # A durable intention must already exist before any mutation.
        self.assertIn(name, self.saved[-1]['attempted'])
        self.writes.append((name, value))
        self.values[name] = value

    def apply(self, accept=lambda: True, write=None):
        from hue_admin.kernel_transaction import apply_kernel
        return apply_kernel(self.values.__getitem__, write or self.write, self.save, accept)

    def test_acceptance_preserves_stronger_baseline_and_unsupported_controls(self):
        self.values['kernel.kptr_restrict'] = 2
        self.values['kernel.dmesg_restrict'] = None
        receipt = self.apply()
        self.assertEqual(receipt['phase'], 'applied')
        self.assertEqual(self.values['kernel.kptr_restrict'], 2)
        self.assertIsNone(self.values['kernel.dmesg_restrict'])
        self.assertEqual(self.writes, [('fs.protected_hardlinks', 1), ('fs.protected_symlinks', 1)])
        self.assertEqual(self.values[ASLR], 2)

    def test_failed_acceptance_restores_original_values(self):
        with self.assertRaisesRegex(ValueError, 'KERNEL_ACCEPTANCE'):
            self.apply(accept=lambda: False)
        self.assertEqual([self.values[name] for name in CONTROLS], [0, 0, 0, 0])
        self.assertEqual(self.saved[-1]['phase'], 'restored')

    def test_write_then_exception_is_also_rolled_back(self):
        def interrupted(name, value):
            self.write(name, value)
            if value == 1:
                raise OSError('simulated device write error')
        with self.assertRaises(OSError):
            self.apply(write=interrupted)
        self.assertEqual(self.values['fs.protected_hardlinks'], 0)
        self.assertEqual(self.saved[-1]['phase'], 'restored')

    def test_failed_readback_does_not_claim_success(self):
        with self.assertRaisesRegex(ValueError, 'KERNEL_READBACK'):
            self.apply(write=lambda name, value: None)
        self.assertEqual(self.saved[-1]['phase'], 'restored')

    def test_recovery_after_process_death_restores_pending_write(self):
        from hue_admin.kernel_transaction import restore_kernel
        def process_dies(name, value):
            self.write(name, value)
            raise SystemExit('simulated process death')
        with self.assertRaises(SystemExit):
            self.apply(write=process_dies)
        receipt = self.saved[-1]
        self.assertEqual(self.values['fs.protected_hardlinks'], 1)
        result = restore_kernel(receipt, self.values.__getitem__, self.write, self.save)
        self.assertEqual(result['phase'], 'restored')
        self.assertEqual(self.values['fs.protected_hardlinks'], 0)
        count = len(self.writes)
        restore_kernel(result, self.values.__getitem__, self.write, self.save)
        self.assertEqual(len(self.writes), count)

    def test_external_change_is_not_overwritten_during_rollback(self):
        def rejected():
            self.values['kernel.kptr_restrict'] = 2
            return False
        with self.assertRaisesRegex(ValueError, 'KERNEL_ROLLBACK_CONFLICT'):
            self.apply(accept=rejected)
        self.assertEqual(self.values['kernel.kptr_restrict'], 2)
        self.assertEqual(self.values['fs.protected_hardlinks'], 0)
        self.assertEqual(self.saved[-1]['phase'], 'rollback_conflict')

    def test_tampered_receipt_is_rejected_before_io(self):
        from hue_admin.kernel_transaction import restore_kernel
        receipt = self.apply()
        for field, value in [('attempted', ['vm.swappiness']), ('phase', 'unknown'),
                             ('schema', True), ('observed', {ASLR: 2})]:
            candidate = copy.deepcopy(receipt)
            candidate[field] = value
            def forbidden(*args):
                self.fail('invalid receipt caused IO')
            with self.subTest(field=field), self.assertRaises(ValueError):
                restore_kernel(candidate, forbidden, forbidden, forbidden)

    def test_failed_durable_save_prevents_first_write(self):
        from hue_admin.kernel_transaction import apply_kernel
        def unavailable(receipt):
            raise OSError('storage unavailable')
        with self.assertRaises(OSError):
            apply_kernel(self.values.__getitem__, self.write, unavailable, lambda: True)
        self.assertEqual(self.writes, [])

    def test_failed_intention_save_does_not_restore_unattempted_control(self):
        from hue_admin.kernel_transaction import apply_kernel
        calls = 0
        def fail_intention(receipt):
            nonlocal calls
            calls += 1
            if calls == 2:
                # Another actor changed the control before our write began.
                self.values['fs.protected_hardlinks'] = 1
                raise OSError('intention not durable')
            self.save(receipt)
        with self.assertRaises(OSError):
            apply_kernel(self.values.__getitem__, self.write, fail_intention, lambda: True)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.values['fs.protected_hardlinks'], 1)

    def test_intention_save_raises_after_persisting_retires_stale_intention(self):
        from hue_admin.kernel_transaction import apply_kernel
        calls = 0
        def fail_after_persist(receipt):
            nonlocal calls
            calls += 1
            self.save(receipt)
            if calls == 2:
                self.values['fs.protected_hardlinks'] = 1
                raise OSError('durability outcome uncertain')
        with self.assertRaises(OSError):
            apply_kernel(self.values.__getitem__, self.write, fail_after_persist, lambda: True)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.values['fs.protected_hardlinks'], 1)
        self.assertEqual(self.saved[-1]['attempted'], [])
        self.assertEqual(self.saved[-1]['phase'], 'restored')

    def test_second_intention_failure_restores_only_first_attempt(self):
        from hue_admin.kernel_transaction import apply_kernel
        calls = 0
        def fail_second_intention(receipt):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.values['fs.protected_symlinks'] = 1
                raise OSError('second intention not durable')
            self.save(receipt)
        with self.assertRaises(OSError):
            apply_kernel(self.values.__getitem__, self.write, fail_second_intention, lambda: True)
        self.assertEqual(self.values['fs.protected_hardlinks'], 0)
        self.assertEqual(self.values['fs.protected_symlinks'], 1)
        self.assertEqual(self.writes, [('fs.protected_hardlinks', 1),
                                       ('fs.protected_hardlinks', 0)])

    def test_change_during_intention_save_is_not_written_or_rolled_back(self):
        from hue_admin.kernel_transaction import apply_kernel
        calls = 0
        def concurrent_save(receipt):
            nonlocal calls
            calls += 1
            self.save(receipt)
            if calls == 2:
                self.values['fs.protected_hardlinks'] = 1
        with self.assertRaisesRegex(ValueError, 'KERNEL_CONCURRENT_CHANGE'):
            apply_kernel(self.values.__getitem__, self.write, concurrent_save, lambda: True)
        self.assertEqual(self.values['fs.protected_hardlinks'], 1)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.saved[-1]['attempted'], [])

    def test_storage_failure_after_mutation_still_attempts_rollback(self):
        from hue_admin.kernel_transaction import apply_kernel
        calls = 0
        def failing_storage(receipt):
            nonlocal calls
            calls += 1
            if calls > 2:
                raise OSError('storage unavailable after first write')
            self.save(receipt)
        with self.assertRaisesRegex(ValueError, 'KERNEL_ROLLBACK_RECORD'):
            apply_kernel(self.values.__getitem__, self.write, failing_storage, lambda: False)
        self.assertEqual(self.values['fs.protected_hardlinks'], 0)
        # Last durable receipt remains sufficient for recovery after a crash.
        self.assertEqual(self.saved[-1]['attempted'], ['fs.protected_hardlinks'])

    def test_transient_restore_record_failure_is_reported_after_safe_restore(self):
        from hue_admin.kernel_transaction import restore_kernel
        receipt = self.apply()
        calls = 0
        def flaky_storage(value):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('temporary storage error')
            self.save(value)
        with self.assertRaisesRegex(ValueError, 'KERNEL_ROLLBACK_RECORD'):
            restore_kernel(receipt, self.values.__getitem__, self.write, flaky_storage)
        self.assertEqual([self.values[name] for name in CONTROLS], [0, 0, 0, 0])
        self.assertEqual(self.saved[-1]['phase'], 'restored')
