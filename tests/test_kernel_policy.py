import unittest


class KernelPolicyTests(unittest.TestCase):
    def baseline(self):
        return {'fs.protected_hardlinks':0,'fs.protected_symlinks':0,
                'kernel.dmesg_restrict':0,'kernel.kptr_restrict':0,'kernel.randomize_va_space':2}

    def test_plan_changes_only_four_approved_keys_and_preserves_aslr(self):
        from hue_admin.kernel_policy import plan_kernel
        value=plan_kernel(self.baseline())
        self.assertEqual(value['changes'],{name:{'before':0,'after':1} for name in self.baseline() if name!='kernel.randomize_va_space'})
        self.assertEqual(value['aslr'],2)

    def test_does_not_weaken_already_stronger_or_enabled_values(self):
        from hue_admin.kernel_policy import plan_kernel
        current={name:1 for name in self.baseline()};current.update({'kernel.kptr_restrict':2,'kernel.randomize_va_space':2})
        self.assertEqual(plan_kernel(current)['changes'],{})

    def test_missing_controls_are_reported_as_unsupported_not_assumed(self):
        from hue_admin.kernel_policy import plan_kernel
        current=self.baseline();current['kernel.dmesg_restrict']=None
        result=plan_kernel(current)
        self.assertEqual(result['unsupported'],['kernel.dmesg_restrict'])
        self.assertNotIn('kernel.dmesg_restrict',result['changes'])

    def test_unknown_mistyped_or_unexpected_values_are_rejected(self):
        from hue_admin.kernel_policy import plan_kernel
        for change in ({'vm.swappiness':1},{'fs.protected_hardlinks':True},
                       {'kernel.kptr_restrict':3},{'fs.protected_symlinks':'1'},
                       {'kernel.randomize_va_space':None}):
            with self.subTest(change=change),self.assertRaises(ValueError):plan_kernel({**self.baseline(),**change})

    def test_aslr_regression_fails_instead_of_silently_changing_policy(self):
        from hue_admin.kernel_policy import plan_kernel
        for value in (0,1):
            with self.assertRaisesRegex(ValueError,'KERNEL_ASLR_BASELINE'):
                plan_kernel({**self.baseline(),'kernel.randomize_va_space':value})
