import inspect
import os
import json
from pathlib import Path
import stat
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch, MagicMock
from hue_admin import storage_operation
from hue_admin.storage_operation import summarize, wait_origins, start, status, dispatch


class ResultTests(unittest.TestCase):
    def test_dispatch_accepts_only_fixed_modes_and_canonical_operation_id(self):
        op_dir=Path('/tmp/hue-storage-operations-rand01')/'00000000-0000-4000-8000-000000000001'
        identifier='00000000-0000-4000-8000-000000000001'
        with patch('hue_admin.storage_operation._find_operation_directory',return_value=op_dir) as finder, \
             patch('hue_admin.storage_operation.status',return_value={'completed':False}) as read, \
             patch('hue_admin.storage_operation.prepare') as preparing:
            self.assertEqual(dispatch(['status',identifier],None,None),{'completed':False})
            finder.assert_called_once_with(identifier)
            read.assert_called_once_with(op_dir);preparing.assert_not_called()
            for args in (['start','--force'],['status','../../opt'],['repair'],['status',identifier.upper()+'x']):
                with self.assertRaisesRegex(ValueError,'STORAGE_OPERATION_ARGUMENTS'):dispatch(args,None,None)

    def test_start_default_root_uses_unpredictable_tmp_dir(self):
        sig=inspect.signature(start)
        self.assertIsNone(sig.parameters['root'].default)
        self.assertEqual(storage_operation.TMP_ROOT,Path('/tmp'))
        fake_fcntl=types.SimpleNamespace(LOCK_EX=2,LOCK_NB=4,flock=lambda fd,flags:None)
        fake_io=types.SimpleNamespace(listener={'pid':100},device=1)
        fake_stat=types.SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=0,st_nlink=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_base=Path(tmpdir)
            fake_journal=MagicMock();fake_journal._directory.return_value=99
            with patch.object(storage_operation,'TMP_ROOT',tmp_base), \
                 patch.object(storage_operation,'RamJournal',return_value=fake_journal) as mock_rj, \
                 patch.object(storage_operation,'boot_id',return_value='11111111-1111-1111-1111-111111111111'), \
                 patch.object(storage_operation,'origins',return_value=()), \
                 patch.object(storage_operation,'_write'), \
                 patch.object(storage_operation,'launch_maintenance',return_value=4321), \
                 patch.object(storage_operation,'process_token',return_value={'pid':4321,'startTicks':10,'pgid':4321}), \
                 patch.object(storage_operation.os,'open',return_value=88), \
                 patch.object(storage_operation.os,'close'), \
                 patch.object(storage_operation.os,'fstat',return_value=fake_stat), \
                 patch.object(storage_operation.os,'O_NOFOLLOW',0,create=True), \
                 patch.object(storage_operation.os,'O_NONBLOCK',0,create=True), \
                 patch.dict(sys.modules,{'fcntl':fake_fcntl}):
                r1=start(fake_io);r2=start(fake_io)
            roots=[Path(c.args[0]) for c in mock_rj.call_args_list if Path(c.args[0]).parent==tmp_base]
            self.assertGreaterEqual(len(roots),2)
            self.assertNotEqual(roots[0],roots[1])
            for root_dir in roots:
                self.assertTrue(root_dir.name.startswith('hue-storage-operations-'))
            self.assertNotEqual(Path(r1['directory']),Path(r2['directory']))

    def test_find_operation_directory_enforces_root_private_non_symlink_dirs(self):
        op_id='00000000-0000-4000-8000-000000000002'
        missing_id='00000000-0000-4000-8000-000000000003'
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_base=Path(tmpdir)
            symlink_parent=tmp_base/'hue-storage-operations-symlink'
            bad_uid_parent=tmp_base/'hue-storage-operations-baduid'
            bad_mode_parent=tmp_base/'hue-storage-operations-badmode'
            good_parent=tmp_base/'hue-storage-operations-good'
            for parent in (symlink_parent,bad_uid_parent,bad_mode_parent,good_parent):
                (parent/op_id).mkdir(parents=True)
            orig_is_symlink=Path.is_symlink
            def fake_is_symlink(p):return True if p==symlink_parent else orig_is_symlink(p)
            fd_map={
                str(bad_uid_parent):(101,types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=1000)),
                str(bad_mode_parent):(102,types.SimpleNamespace(st_mode=stat.S_IFDIR|0o777,st_uid=0)),
                str(good_parent):(103,types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0)),
                (103,op_id):(104,types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0)),
            }
            fd_stats={fd:st for fd,st in fd_map.values()}
            def fake_open(path,flags,dir_fd=None):
                key=(dir_fd,str(path)) if dir_fd is not None else str(path)
                if key not in fd_map:raise FileNotFoundError(key)
                return fd_map[key][0]
            with patch.object(storage_operation,'TMP_ROOT',tmp_base), \
                 patch.object(Path,'is_symlink',fake_is_symlink), \
                 patch.object(storage_operation.os,'O_DIRECTORY',0x10000,create=True), \
                 patch.object(storage_operation.os,'O_NOFOLLOW',0x20000,create=True), \
                 patch.object(storage_operation.os,'open',side_effect=fake_open), \
                 patch.object(storage_operation.os,'fstat',side_effect=lambda fd:fd_stats[fd]), \
                 patch.object(storage_operation.os,'close'):
                self.assertEqual(storage_operation._find_operation_directory(op_id),good_parent/op_id)
                with self.assertRaises(FileNotFoundError):
                    storage_operation._find_operation_directory(missing_id)
                good_dup=tmp_base/'hue-storage-operations-good2'
                (good_dup/op_id).mkdir(parents=True)
                fd_map[str(good_dup)]=(105,types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0))
                fd_map[(105,op_id)]=(106,types.SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0))
                fd_stats[105]=fd_map[str(good_dup)][1];fd_stats[106]=fd_map[(105,op_id)][1]
                with self.assertRaisesRegex(ValueError,'STORAGE_OPERATION_AMBIGUOUS'):
                    storage_operation._find_operation_directory(op_id)

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
