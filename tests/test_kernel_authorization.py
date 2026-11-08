import unittest
from unittest.mock import patch


class KernelAuthorizationTests(unittest.TestCase):
    @patch('hue_admin.service_manager.selection', return_value='0.2.0-rc.7')
    @patch('hue_admin.service_manager.verify_selected')
    @patch('hue_admin.runtime_policy.release_policy', return_value={'kernel': True})
    def test_verified_enabled_unchanged_selection_is_returned(self, policy, verify, selection):
        from hue_admin.kernel_authorization import authorized_release
        self.assertEqual(authorized_release('/fixture'), '0.2.0-rc.7')

    @patch('hue_admin.service_manager.selection', return_value='0.2.0-rc.7')
    @patch('hue_admin.service_manager.verify_selected')
    @patch('hue_admin.runtime_policy.release_policy', return_value={'kernel': False})
    def test_disabled_signed_policy_blocks_kernel_access(self, policy, verify, selection):
        from hue_admin.kernel_authorization import authorized_release
        with self.assertRaisesRegex(ValueError, 'KERNEL_POLICY_DISABLED'):
            authorized_release('/fixture')

    @patch('hue_admin.service_manager.selection', return_value='0.2.0-rc.7')
    @patch('hue_admin.service_manager.verify_selected', side_effect=ValueError('BAD_SLOT'))
    @patch('hue_admin.runtime_policy.release_policy', return_value={'kernel': True})
    def test_bad_selected_slot_blocks_even_enabled_policy(self, policy, verify, selection):
        from hue_admin.kernel_authorization import authorized_release
        with self.assertRaisesRegex(ValueError, 'BAD_SLOT'):
            authorized_release('/fixture')

    @patch('hue_admin.service_manager.selection', side_effect=['0.2.0-rc.7', '0.2.0-rc.8'])
    @patch('hue_admin.service_manager.verify_selected')
    @patch('hue_admin.runtime_policy.release_policy', return_value={'kernel': True})
    def test_selection_change_during_check_is_rejected(self, policy, verify, selection):
        from hue_admin.kernel_authorization import authorized_release
        with self.assertRaisesRegex(ValueError, 'KERNEL_SELECTION_CHANGED'):
            authorized_release('/fixture')
