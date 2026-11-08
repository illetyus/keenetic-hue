from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import patch

from hue_admin.acceptance_entry import launch_from_slot, entry_candidate


class EntryTests(unittest.TestCase):
    def test_only_fixed_canonical_signed_slot_path_is_accepted(self):
        root=Path('/fixture').absolute()
        self.assertEqual(entry_candidate(root/'releases/0.2.0-rc.7/service/acceptance-entry.py',root),'0.2.0-rc.7')
        for path in (root/'manager/acceptance-entry.py',root/'releases/bad/service/acceptance-entry.py',
                     root/'releases/0.2.0-rc.7/service/other.py'):
            with self.subTest(path=path),self.assertRaises(ValueError):entry_candidate(path,root)

    def test_storage_gate_failure_never_detaches_or_mutates_candidate(self):
        root=Path('/fixture').absolute()
        with patch('hue_admin.acceptance_entry.admit',return_value=nullcontext()), \
             patch('hue_admin.acceptance_entry.recover_before_start'), \
             patch('hue_admin.acceptance_entry.verified_candidate'), \
             patch('hue_admin.acceptance_entry.manager.selection',return_value='0.2.0-rc.5'), \
             patch('hue_admin.acceptance_entry.require_current',return_value=False), \
             patch('hue_admin.acceptance_entry.launch_candidate') as detach:
            with self.assertRaisesRegex(ValueError,'STORAGE_ACCEPTANCE_REQUIRED'):
                launch_from_slot('0.2.0-rc.7',root/'backups/candidates/test.huerel','a'*64,root=root)
            detach.assert_not_called()

    def test_started_reply_is_not_success_and_uses_fixed_storage_and_observer(self):
        root=Path('/fixture').absolute()
        with patch('hue_admin.acceptance_entry.admit',return_value=nullcontext()), \
             patch('hue_admin.acceptance_entry.recover_before_start'), \
             patch('hue_admin.acceptance_entry.verified_candidate') as verify, \
             patch('hue_admin.acceptance_entry.manager.selection',return_value='0.2.0-rc.5'), \
             patch('hue_admin.acceptance_entry.require_current',return_value=True) as gate, \
             patch('hue_admin.acceptance_entry.physical_snapshot',return_value={'on':False,'brightnessRaw':50}) as observe, \
             patch('hue_admin.acceptance_entry.ManagedRelease.preflight'), \
             patch('hue_admin.acceptance_entry.launch_candidate',return_value=123) as detach:
            result=launch_from_slot('0.2.0-rc.7',root/'backups/candidates/test.huerel','a'*64,root=root)
            self.assertEqual(result['status'],'started')
            self.assertNotIn('success',result)
            verify.assert_called_once_with(root,'0.2.0-rc.7')
            adapter,journal,baseline,candidate,operation=detach.call_args.args
            self.assertTrue(adapter.storage_ready());gate.assert_called_once_with(root)
            self.assertEqual(adapter.physical(),{'on':False,'brightnessRaw':50});observe.assert_called_once_with(root)
            self.assertEqual((baseline,candidate),('0.2.0-rc.5','0.2.0-rc.7'))
            self.assertEqual(operation,result['operationId'])

    def test_missing_boot_recovery_blocks_even_a_verified_candidate(self):
        import json
        from hue_admin.acceptance_entry import verified_candidate
        root=Path('/fixture').absolute()
        candidate='0.2.0-rc.7'
        manifest=json.dumps({'files':{'manager/hue_admin/__init__.py':{}}}).encode()
        with patch('hue_admin.release_commands.trusted_key',return_value=b'a'*32), \
             patch('hue_admin.release_commands.runtime_from_manifest',return_value=('mips-3.4','cp313',{})), \
             patch('hue_admin.acceptance_entry.manager.private_bytes',side_effect=lambda path,*_:manifest if path.name=='verified-manifest.json' else b'# code'), \
             patch('hue_admin.activation.verify_slot',return_value=root/'releases'/candidate), \
             patch('hue_admin.runtime_policy.release_policy',return_value={'schema':1,'agent':'dedicated','tunnel':'dedicated','childIdentity':True,'kernel':False}), \
             patch('hue_admin.bootstrap_install.manager_matches',return_value=False):
            with self.assertRaisesRegex(ValueError,'ACCEPTANCE_BOOT_RECOVERY_NOT_INSTALLED'):
                verified_candidate(root,candidate)


if __name__=='__main__':unittest.main()
