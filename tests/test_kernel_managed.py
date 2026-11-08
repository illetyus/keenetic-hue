import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


class KernelManagedTests(unittest.TestCase):
    def test_restore_verifies_selected_slot_and_holds_lock(self):
        from hue_admin.kernel_managed import restore_managed
        events = []
        @contextmanager
        def lock(root):
            events.append('lock')
            yield
            events.append('unlock')
        def verify(root, release):
            events.append(('verify', release))
        def restore(root, target):
            events.append(('restore', target))
            return {'phase': 'restored'}
        with patch('hue_admin.kernel_managed.management_lock', lock), \
             patch('hue_admin.kernel_managed.selection', return_value='0.2.0-rc.7'), \
             patch('hue_admin.kernel_managed.verify_selected', verify), \
             patch('hue_admin.kernel_managed.restore_policy', restore):
            self.assertEqual(restore_managed(), {'phase': 'restored'})
        self.assertEqual(events, ['lock', ('verify', '0.2.0-rc.7'),
                                  ('restore', Path('/proc/sys')), 'unlock'])

    def test_restore_does_not_run_when_selection_changes(self):
        from hue_admin.kernel_managed import restore_managed
        @contextmanager
        def lock(root):
            yield
        with patch('hue_admin.kernel_managed.management_lock', lock), \
             patch('hue_admin.kernel_managed.selection', side_effect=['0.2.0-rc.7', '0.2.0-rc.8']), \
             patch('hue_admin.kernel_managed.verify_selected'), \
             patch('hue_admin.kernel_managed.restore_policy') as restore:
            with self.assertRaisesRegex(ValueError, 'KERNEL_SELECTION_CHANGED'):
                restore_managed()
            restore.assert_not_called()

    def test_ensure_holds_lock_and_requires_authorization(self):
        from hue_admin.kernel_managed import ensure_managed
        events = []
        @contextmanager
        def lock(root):
            events.append('lock')
            yield
            events.append('unlock')
        def authorize(root):
            events.append('authorize')
        def ensure(root, target, accept):
            events.append(('ensure', target))
            return {'phase': 'applied'}
        with patch('hue_admin.kernel_managed.management_lock', lock), \
             patch('hue_admin.kernel_managed.authorized_release', authorize), \
             patch('hue_admin.kernel_managed.ensure_policy', ensure):
            self.assertEqual(ensure_managed(lambda: True), {'phase': 'applied'})
        self.assertEqual(events, ['lock', 'authorize', ('ensure', Path('/proc/sys')), 'unlock'])

    def test_ensure_rejects_disabled_policy_before_kernel_access(self):
        from hue_admin.kernel_managed import ensure_managed
        @contextmanager
        def lock(root):
            yield
        with patch('hue_admin.kernel_managed.management_lock', lock), \
             patch('hue_admin.kernel_managed.authorized_release', side_effect=ValueError('disabled')), \
             patch('hue_admin.kernel_managed.ensure_policy') as ensure:
            with self.assertRaisesRegex(ValueError, 'disabled'):
                ensure_managed(lambda: True)
            ensure.assert_not_called()

    def test_lock_and_authorization_cover_entire_apply(self):
        from hue_admin.kernel_managed import apply_managed
        events = []

        @contextmanager
        def lock(root):
            events.append('lock')
            yield
            events.append('unlock')

        def authorize(root):
            events.append('authorize')
            return '0.2.0-rc.7'

        def apply(root, target, accept):
            events.append(('apply', target))
            return {'phase': 'applied'}

        with patch('hue_admin.kernel_managed.management_lock', lock), \
             patch('hue_admin.kernel_managed.authorized_release', authorize), \
             patch('hue_admin.kernel_managed.apply_policy', apply):
            self.assertEqual(apply_managed(lambda: True), {'phase': 'applied'})
        self.assertEqual(events, ['lock', 'authorize', ('apply', Path('/proc/sys')), 'unlock'])

    def test_failed_authorization_never_opens_kernel_target(self):
        from hue_admin.kernel_managed import apply_managed

        @contextmanager
        def lock(root):
            yield

        with patch('hue_admin.kernel_managed.management_lock', lock), \
             patch('hue_admin.kernel_managed.authorized_release', side_effect=ValueError('disabled')), \
             patch('hue_admin.kernel_managed.apply_policy') as apply:
            with self.assertRaisesRegex(ValueError, 'disabled'):
                apply_managed(lambda: True)
            apply.assert_not_called()

    def test_reconcile_checks_signed_slot_without_requiring_kernel_enabled(self):
        from hue_admin.kernel_managed import reconcile_managed
        events = []
        @contextmanager
        def lock(root):
            events.append('lock')
            yield
            events.append('unlock')
        def verify(root, release):
            events.append(('verify', release))
        def reconcile(root, target):
            events.append(('reconcile', target))
            return {'phase': 'restored'}
        with patch('hue_admin.kernel_managed.management_lock', lock), \
             patch('hue_admin.kernel_managed.selection', return_value='0.2.0-rc.7'), \
             patch('hue_admin.kernel_managed.verify_selected', verify), \
             patch('hue_admin.kernel_managed.reconcile_policy', reconcile):
            self.assertEqual(reconcile_managed(), {'phase': 'restored'})
        self.assertEqual(events, ['lock', ('verify', '0.2.0-rc.7'),
                                  ('reconcile', Path('/proc/sys')), 'unlock'])

    def test_reconcile_rejects_changed_or_unverified_selection(self):
        from hue_admin.kernel_managed import reconcile_managed
        @contextmanager
        def lock(root):
            yield
        for failure in ('signature', 'selection'):
            choices = ['0.2.0-rc.7', '0.2.0-rc.8'] if failure == 'selection' else ['0.2.0-rc.7']
            with self.subTest(failure=failure), \
                 patch('hue_admin.kernel_managed.management_lock', lock), \
                 patch('hue_admin.kernel_managed.selection', side_effect=choices), \
                 patch('hue_admin.kernel_managed.verify_selected',
                       side_effect=ValueError('BAD_SLOT') if failure == 'signature' else None), \
                 patch('hue_admin.kernel_managed.reconcile_policy') as reconcile:
                with self.assertRaises(ValueError):
                    reconcile_managed()
                reconcile.assert_not_called()
