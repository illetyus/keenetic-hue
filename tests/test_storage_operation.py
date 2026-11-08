import os
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock
from hue_admin.storage_operation import summarize, wait_origins, start, status, dispatch


class ResultTests(unittest.TestCase):
    def test_dispatch_accepts_only_fixed_modes_and_canonical_operation_id(self):
        root=Path('/tmp/hue-storage-operations');identifier='00000000-0000-4000-8000-000000000001'
        with patch('hue_admin.storage_operation.status',return_value={'completed':False}) as read, \
             patch('hue_admin.storage_operation.prepare') as preparing:
            self.assertEqual(dispatch(['status',identifier],None,None),{'completed':False})
            read.assert_called_once_with(root/identifier);preparing.assert_not_called()
            for args in (['start','--force'],['status','../../opt'],['repair'],['status',identifier.upper()+'x']):
                with self.assertRaisesRegex(ValueError,'STORAGE_OPERATION_ARGUMENTS'):dispatch(args,None,None)

    def test_start_dispatch_owns_prepare_context_through_fd_handoff(self):
        with patch('hue_admin.storage_operation.prepare') as preparing, \
             patch('hue_admin.storage_operation.start',return_value={'started':True}) as launch:
            context=MagicMock();preparing.return_value=context
            self.assertEqual(dispatch(['start'],'runtime','factory'),{'started':True})
            launch.assert_called_once_with(context.__enter__.return_value)
            context.__exit__.assert_called_once()

    def test_closed_journal_alone_never_means_success(self):
        closed={'phase':'closed','clean':True,'restoreVerified':True}
        self.assertFalse(summarize(closed,None,True)['completed'])
        self.assertEqual(summarize(closed,None,False)['phase'],'interrupted')
        self.assertFalse(summarize(closed,1,False)['completed'])
        self.assertTrue(summarize(closed,0,False)['completed'])
        self.assertFalse(summarize({'phase':'unclean_readonly'},0,False)['completed'])
        self.assertFalse(summarize(None,None,None)['terminal'])
        self.assertEqual(summarize(None,None,None)['phase'],'unknown')

    def test_wait_only_observes_original_tokens_never_signals_processes(self):
        token={'pid':11,'pgid':11,'startTicks':1}
        with patch('hue_admin.storage_operation.process_token',side_effect=[token,None]),patch('hue_admin.storage_operation.time.sleep'):
            wait_origins((token,),timeout=1)
        with patch('hue_admin.storage_operation.process_token',return_value=token):
            with self.assertRaisesRegex(RuntimeError,'STORAGE_ORIGIN_STILL_ACTIVE'):
                wait_origins((token,),timeout=0)


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Native root operation fixture')
class NativeOperationTests(unittest.TestCase):
    def test_launcher_exits_before_stop_and_result_survives_parent_exit(self):
        from hue_admin.acceptance_journal import process_token
        from test_storage_maintenance import IO
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            root=Path(directory);root.chmod(0o700);marker=root/'stopped'
            class Fake(IO):
                def prepare_process(self):pass
                def stop(self):marker.touch();return True
                def recovery_ready(self):return True
            parent=os.fork()
            if parent==0:
                try:
                    launched=start(Fake(),root,origin_tokens=(process_token(os.getpid()),))
                    (root/'launched.json').write_text(json.dumps(launched))
                    time.sleep(.2)
                    os._exit(1 if marker.exists() else 0)
                except BaseException:os._exit(2)
            _,code=os.waitpid(parent,0);self.assertEqual(code,0)
            launched=json.loads((root/'launched.json').read_text());until=time.monotonic()+6
            while not (value:=status(Path(launched['directory'])))['terminal']:
                if time.monotonic()>until:self.fail('detached operation outcome missing')
                time.sleep(.02)
            self.assertTrue(marker.exists());self.assertTrue(value['completed'])

    def test_detached_result_and_single_operation_lock(self):
        from test_storage_maintenance import IO
        class Fake(IO):
            def prepare_process(self):pass
            def check(self):time.sleep(.25);return {}
            def recovery_ready(self):return True
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            root=Path(directory);root.chmod(0o700)
            launched=start(Fake(),root,origin_tokens=())
            with self.assertRaisesRegex(RuntimeError,'STORAGE_OPERATION_BUSY'):
                start(Fake(),root,origin_tokens=())
            until=time.monotonic()+6
            while not (value:=status(Path(launched['directory'])))['terminal']:
                if time.monotonic()>until:self.fail('operation outcome missing')
                time.sleep(.02)
            _,code=os.waitpid(launched['guardianPid'],0)
            self.assertEqual(code,0);self.assertTrue(value['completed'])
            self.assertEqual((Path(launched['directory'])/'result.json').stat().st_mode&0o777,0o600)


if __name__=='__main__':unittest.main()
