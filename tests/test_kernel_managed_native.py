import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from hue_admin.installation import install_candidate
from hue_admin.releases import build_release, verify_release
from hue_admin.restore_current import management_lock
from hue_admin.kernel_controller import apply_policy, restore_policy, reconcile_policy, ensure_policy
from hue_admin.kernel_managed import apply_managed, reconcile_managed, ensure_managed, restore_managed
from hue_admin.kernel_receipt import KernelReceipt


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'POSIX root fixture')
class KernelManagedNativeTests(unittest.TestCase):
    def test_signed_disabled_policy_allows_safe_prior_boot_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory); root = base / 'system'; target = base / 'fake-sysctl'
            for name in ('fs', 'kernel'):
                (target / name).mkdir(parents=True)
            for name in ('fs/protected_hardlinks', 'fs/protected_symlinks',
                         'kernel/dmesg_restrict', 'kernel/kptr_restrict'):
                (target / name).write_text('0\n')
            (target / 'kernel/randomize_va_space').write_text('2\n')
            key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            runtime = ('mips-3.4', f'cp{sys.version_info.major}{sys.version_info.minor}',
                       {'bumble': '0.0.236'})
            policy = {'schema': 1, 'agent': 'dedicated', 'tunnel': 'dedicated',
                      'childIdentity': True, 'kernel': False}
            archive = base / 'fixture.huerel'
            build_release({'metadata/runtime-policy.json': json.dumps(policy).encode(),
                           'agent/hue_agent/__init__.py': b'fixture'}, archive, key,
                          '0.2.0-rc.7', *runtime, release_sequence=7)
            install_candidate(verify_release(archive, public, *runtime), root, 'fresh')
            (root / 'active.json').write_text('{"schema":1,"releaseId":"0.2.0-rc.7"}')
            (root / 'active.json').chmod(0o600)
            previous = {'schema': 1, 'phase': 'applying', 'attempted': ['fs.protected_hardlinks'],
                        'observed': {'fs.protected_hardlinks': 0, 'fs.protected_symlinks': 0,
                                     'kernel.dmesg_restrict': 0, 'kernel.kptr_restrict': 0,
                                     'kernel.randomize_va_space': 2}}
            with patch('hue_admin.kernel_receipt._boot_id', return_value='11111111-1111-1111-1111-111111111111'):
                KernelReceipt(root).save(previous)
            def fixture_target(actual_root, actual_target):
                self.assertEqual(actual_target, Path('/proc/sys'))
                return reconcile_policy(actual_root, target)
            def fixture_restore(actual_root, actual_target):
                self.assertEqual(actual_target, Path('/proc/sys'))
                return restore_policy(actual_root, target)
            with patch('hue_admin.kernel_managed.SYSTEM', root), \
                 patch('hue_admin.kernel_managed.reconcile_policy', fixture_target), \
                 patch('hue_admin.kernel_managed.restore_policy', fixture_restore), \
                 patch('hue_admin.platform.runtime_for', return_value=runtime), \
                 patch('hue_admin.kernel_receipt._boot_id', return_value='22222222-2222-2222-2222-222222222222'):
                self.assertEqual(reconcile_managed()['phase'], 'restored')
                with self.assertRaisesRegex(ValueError, 'KERNEL_POLICY_DISABLED'):
                    apply_managed(lambda: True)
                KernelReceipt(root).save(previous)
                (target / 'fs/protected_hardlinks').write_text('1\n')
                slot = root / 'releases/0.2.0-rc.7/agent/hue_agent/__init__.py'
                slot.write_bytes(b'corrupted')
                with self.assertRaises(ValueError):
                    restore_managed()
                self.assertEqual((target / 'fs/protected_hardlinks').read_text(), '1\n')
                with management_lock(root):
                    install_candidate(verify_release(archive, public, *runtime), root, 'repair',
                                      stop_active=lambda: True)
                self.assertEqual(restore_managed()['phase'], 'restored')
                self.assertEqual((target / 'fs/protected_hardlinks').read_text(), '0\n')

    def test_signed_apply_holds_real_lock_and_blocks_concurrent_management(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / 'system'
            target = base / 'fake-sysctl'
            for name in ('fs', 'kernel'):
                (target / name).mkdir(parents=True)
            for name in ('fs/protected_hardlinks', 'fs/protected_symlinks',
                         'kernel/dmesg_restrict', 'kernel/kptr_restrict'):
                (target / name).write_text('0\n')
            (target / 'kernel/randomize_va_space').write_text('2\n')
            key = Ed25519PrivateKey.generate()
            public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            runtime = ('mips-3.4', f'cp{sys.version_info.major}{sys.version_info.minor}',
                       {'bumble': '0.0.236'})
            policy = {'schema': 1, 'agent': 'dedicated', 'tunnel': 'dedicated',
                      'childIdentity': True, 'kernel': True}
            archive = base / 'fixture.huerel'
            build_release({'metadata/runtime-policy.json': json.dumps(policy).encode(),
                           'agent/hue_agent/__init__.py': b'fixture'}, archive, key,
                          '0.2.0-rc.7', *runtime, release_sequence=7)
            install_candidate(verify_release(archive, public, *runtime), root, 'fresh')
            (root / 'active.json').write_text('{"schema":1,"releaseId":"0.2.0-rc.7"}')
            (root / 'active.json').chmod(0o600)
            def acceptance():
                with self.assertRaisesRegex(RuntimeError, 'MANAGEMENT_BUSY'):
                    with management_lock(root):
                        pass
                return True
            def fixture_target(actual_root, actual_target, accept):
                self.assertEqual(actual_target, Path('/proc/sys'))
                return apply_policy(actual_root, target, accept)
            with patch('hue_admin.kernel_managed.SYSTEM', root), \
                 patch('hue_admin.kernel_managed.apply_policy', fixture_target), \
                 patch('hue_admin.platform.runtime_for', return_value=runtime):
                self.assertEqual(apply_managed(acceptance)['phase'], 'applied')
            self.assertEqual((target / 'fs/protected_hardlinks').read_text(), '1\n')
            def fixture_ensure(actual_root, actual_target, accept):
                self.assertEqual(actual_target, Path('/proc/sys'))
                return ensure_policy(actual_root, target, accept)
            with patch('hue_admin.kernel_managed.SYSTEM', root), \
                 patch('hue_admin.kernel_managed.ensure_policy', fixture_ensure), \
                 patch('hue_admin.platform.runtime_for', return_value=runtime):
                self.assertEqual(ensure_managed(lambda: False)['phase'], 'applied')
            with management_lock(root):
                self.assertEqual(restore_policy(root, target)['phase'], 'restored')
            self.assertEqual((target / 'fs/protected_hardlinks').read_text(), '0\n')
