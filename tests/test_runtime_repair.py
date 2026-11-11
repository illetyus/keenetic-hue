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

    def test_repair_validates_usb_and_ready_state_before_writing_prepared_receipt(self):
        from hue_admin.runtime_repair import repair_runtime
        from hue_admin.service_manager import agent_paths
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('state','config','secrets','backups','run','releases'):(root/name).mkdir()
            (root/'state/service-identities.json').write_text(json.dumps({
                'hue-tunnel':{'schema':1,'uid':61000,'gid':61000},
                'hue-agent':{'schema':1,'uid':61001,'gid':61001}}))
            cfg=agent_paths(root);cfg.update(transport='usb:fixture',address='lamp',controller_public_address='adapter',
                                           port=8787,cloud={'enabled':False},tunnel={'enabled':True,'port':8788,'token_file':str(root/'secrets/device.token')})
            (root/'config/agent.json').write_text(json.dumps(cfg))
            (root/'secrets/tunnel.token').write_text('t'*64)
            manifest={'schema':1,'releaseId':'0.2.0-rc.15','architecture':'mips-3.4','pythonAbi':'cp313',
                      'dependencies':{'bumble':'0.0.236'},'files':{'bin/cloudflared':{'size':10,'sha256':'a'*64},'agent/hue_agent/__init__.py':{'size':10,'sha256':'a'*64}}}
            policy={'schema':1,'agent':'dedicated','tunnel':'dedicated','childIdentity':True,'kernel':False}
            from types import SimpleNamespace
            with patch('hue_admin.runtime_repair.os',SimpleNamespace(name='posix',geteuid=lambda:0)), \
                 patch('hue_admin.installation.private_directory'), \
                 patch('hue_admin.service_manager.selection',return_value='0.2.0-rc.15'), \
                 patch('hue_admin.service_manager.verify_selected',return_value='0.2.0-rc.15'), \
                 patch('hue_admin.runtime_policy.release_policy',return_value=policy), \
                 patch('hue_admin.runtime_policy.authenticated_manifest',return_value=manifest), \
                 patch('hue_admin.service_manager.owner_record',return_value=None), \
                 patch('hue_admin.service_manager.supervisor_owned',return_value=False), \
                 patch('hue_admin.service_manager.child_pid',return_value=None), \
                 patch('hue_admin.service_manager.private_bytes',side_effect=lambda p,limit=8192:Path(p).read_bytes()), \
                 patch('hue_admin.agent_runtime._master',side_effect=lambda p,limit=65536:Path(p).read_bytes()):
                with self.assertRaises(FileNotFoundError):
                    repair_runtime(root,'0.2.0-rc.15')
                self.assertFalse((root/'state/runtime-repair.json').exists())
                (root/'config/usb.json').write_text(json.dumps({'schema':1,'vendor':'050d','product':'065a','serial':'AABBCCDDEEFF'}))
                (root/'state/agent-runtime.json').write_text(json.dumps({
                    'schema':1,'phase':'ready','uid':61001,'gid':61001,'bondHash':'a'*64,'journalHash':'b'*64}))
                with patch('hue_admin.runtime_repair._directory') as mdir, \
                     patch('hue_admin.privileges.prepare_tunnel_runtime'), \
                     patch('hue_admin.agent_runtime.prepare_agent_runtime'):
                    def dir_effect(path,*args,**kwargs):
                        if not kwargs.get('root_owned',True) and not Path(path).exists():
                            raise ValueError('RUNTIME_REPAIR_STATE_MISSING')
                    mdir.side_effect=dir_effect
                    with self.assertRaisesRegex(ValueError,'RUNTIME_REPAIR_STATE_MISSING'):
                        repair_runtime(root,'0.2.0-rc.15')
                    self.assertFalse((root/'state/runtime-repair.json').exists())
                (root/'state/agent-runtime.json').unlink()
                with patch('hue_admin.runtime_repair._directory') as mdir, \
                     patch('hue_admin.privileges.prepare_tunnel_runtime'), \
                     patch('hue_admin.agent_runtime.prepare_agent_runtime'):
                    def dir_effect(path,*args,**kwargs):
                        if not kwargs.get('root_owned',True) and not Path(path).exists():
                            raise ValueError('RUNTIME_REPAIR_STATE_MISSING')
                    mdir.side_effect=dir_effect
                    res=repair_runtime(root,'0.2.0-rc.15')
                    self.assertTrue(res['runtimePrepared'])


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
