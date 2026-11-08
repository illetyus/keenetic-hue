from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hue_admin.runtime_repair import receipt,recover_if_needed
from hue_admin.restore_current import restore_from


class RuntimeRepairTests(unittest.TestCase):
    def pending(self,root):
        (root/'state').mkdir()
        path=root/'state/runtime-repair.json'
        value={'schema':1,'phase':'prepared','releaseId':'0.2.0-rc.6',
               'backup':'backups/runtime-repair-'+'a'*32}
        path.write_text(json.dumps(value));path.chmod(0o600)
        return path,value

    def test_legacy_without_receipt_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.assertIsNone(receipt(root));self.assertFalse(recover_if_needed(root))

    def test_invalid_receipt_never_supplies_arbitrary_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path,value=self.pending(root)
            for change in ({'schema':True},{'backup':'../../etc'},{'phase':'unknown'},
                           {'extra':0},{'releaseId':'../../old'}):
                path.write_text(json.dumps({**value,**change}))
                with self.assertRaises(ValueError):receipt(root)

    def test_restore_refuses_to_cross_unfinished_runtime_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);self.pending(root)
            with patch('hue_admin.restore_current.SYSTEM',root), \
                 patch('hue_admin.restore_current.management_lock',return_value=nullcontext()), \
                 patch('hue_admin.restore_current.verify_backup') as verify:
                with self.assertRaisesRegex(RuntimeError,'RUNTIME_REPAIR_RECOVERY_REQUIRED'):
                    restore_from('unused.huebak','fixture-password',False,lambda *_:True,lambda:{})
                verify.assert_not_called()


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'Native repair FD ownership')
class RuntimeRepairOwnershipTests(unittest.TestCase):
    def test_mutable_directory_with_wrong_owner_is_not_adopted(self):
        from hue_admin.runtime_repair import _directory
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'state';path.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError,'RUNTIME_REPAIR_DIRECTORY_OWNER'):
                _directory(path,62001,62001,0o700,path.stat().st_dev,root_owned=False)
            self.assertEqual(path.stat().st_uid,0)

    def test_directory_symlink_is_rejected_without_changing_target(self):
        from hue_admin.runtime_repair import _directory
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'target';target.mkdir(mode=0o755);target.chmod(0o755)
            link=root/'link';link.symlink_to(target)
            with self.assertRaisesRegex(ValueError,'RUNTIME_REPAIR_SYMLINK'):
                _directory(link,0,0,0o700,target.stat().st_dev,root_owned=True)
            self.assertEqual(target.stat().st_mode&0o777,0o755)
