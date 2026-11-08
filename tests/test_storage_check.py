import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock

from hue_admin.storage_check import Runtime, check_offline, mounted_targets, publish


class ContractTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','POSIX dev_t')
    def test_mount_inventory_uses_device_identity_not_path_substrings(self):
        text=('1 0 8:1 / /opt rw - ext4 /dev/sda1 rw\n'
              '2 0 8:1 / /tmp/mnt/usb rw - ext4 /dev/sda1 rw\n'
              '3 0 8:10 / /other rw - ext4 /dev/sda10 rw\n')
        self.assertEqual(mounted_targets(os.makedev(8,1),text),['/opt','/tmp/mnt/usb'])

    def test_formatter_and_escaping_runtime_entries_are_rejected(self):
        base={'schema':1,'toolVersion':'1.47.3',
              'files':{'e2fsck':'a'*64,'ld.so.1':'b'*64},'links':{}}
        for files in ({'../e2fsck':'a'*64},{'mke2fs':'a'*64},
                      {'e2fsck':'bad','ld.so.1':'b'*64}):
            with self.subTest(files=files),self.assertRaises(ValueError):
                Runtime('/tmp/not-created',{**base,'files':files})
        with self.assertRaises(ValueError):
            Runtime('/tmp/not-created',{**base,'links':{'libbad.so.1':'/opt/lib/libc.so.6'}})

    def test_failed_evidence_is_rejected_before_any_publication_file_open(self):
        from test_storage_acceptance import evidence,IDENTITY,BOOT
        value=evidence();value['exitCode']=4
        with patch('hue_admin.storage_check.current_storage',return_value=IDENTITY), \
             patch('hue_admin.storage_check.boot_id',return_value=BOOT), \
             patch('hue_admin.storage_check.time.monotonic',return_value=150), \
             patch('hue_admin.storage_check.os.open') as opened:
            with self.assertRaises(ValueError):publish('/never-written',value)
            opened.assert_not_called()

    @unittest.skipUnless(os.name=='posix','POSIX real block gate')
    def test_regular_image_cannot_create_evidence_or_start_tool(self):
        with tempfile.TemporaryFile() as stream:
            runtime=Mock()
            with self.assertRaisesRegex(ValueError,'STORAGE_BLOCK_REQUIRED'):
                check_offline(stream.fileno(),{},runtime)
            runtime.verify.assert_not_called()
            runtime.run.assert_not_called()

    @unittest.skipUnless(os.name=='posix','POSIX mocked block gate')
    def test_mounted_device_is_rejected_before_runtime_execution(self):
        runtime=Mock()
        fake=Mock(st_mode=0o060000,st_rdev=os.makedev(8,1))
        with patch('hue_admin.storage_check.os.fstat',return_value=fake), \
             patch('hue_admin.storage_check.readonly_fd',return_value=True), \
             patch('hue_admin.storage_check.mounted_targets',return_value=['/opt']):
            with self.assertRaisesRegex(ValueError,'STORAGE_STILL_MOUNTED'):
                check_offline(10,{},runtime)
        runtime.verify.assert_not_called();runtime.run.assert_not_called()

    @unittest.skipUnless(os.name=='posix','POSIX mocked exclusive claim')
    def test_unclean_tool_result_cannot_generate_evidence(self):
        from test_storage_acceptance import IDENTITY,BOOT
        runtime=Mock();runtime.run.return_value={'exitCode':4,'outputSha256':'a'*64}
        fake=Mock(st_mode=0o060000,st_rdev=os.makedev(8,1))
        with patch('hue_admin.storage_check.os.fstat',return_value=fake), \
             patch('hue_admin.storage_check.readonly_fd',return_value=True), \
             patch('hue_admin.storage_check.mounted_targets',return_value=[]), \
             patch('hue_admin.storage_check.identity_from_fd',return_value=IDENTITY), \
             patch('hue_admin.storage_check.boot_id',return_value=BOOT), \
             patch('hue_admin.storage_check.os.open',return_value=11) as opened, \
             patch('hue_admin.storage_check.os.close') as closed:
            with self.assertRaisesRegex(RuntimeError,'STORAGE_CHECK_UNCLEAN'):
                check_offline(10,IDENTITY,runtime)
            self.assertTrue(opened.call_args.args[1]&os.O_EXCL)
            runtime.run.assert_called_once_with(11,600)
            runtime.verify_files.assert_not_called()
            closed.assert_called_once_with(11)


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Native root publication fixture')
class PublicationTests(unittest.TestCase):
    def test_only_private_atomic_evidence_file_is_written_after_validation(self):
        import json
        from test_storage_acceptance import evidence,IDENTITY,BOOT
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);root.chmod(0o700);(root/'state').mkdir(mode=0o700)
            value=evidence()
            with patch('hue_admin.storage_check.current_storage',return_value=IDENTITY), \
                 patch('hue_admin.storage_check.boot_id',return_value=BOOT), \
                 patch('hue_admin.storage_check.time.monotonic',return_value=150):
                publish(root,value)
                path=root/'state/storage-acceptance.json'
                self.assertEqual(json.loads(path.read_text()),value)
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                path.chmod(0o644)
                with self.assertRaises(ValueError):publish(root,value)


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Native launch cancellation')
class CancellationTests(unittest.TestCase):
    def test_interrupt_after_child_creation_stops_child_before_returning(self):
        import signal,subprocess,sys
        import hue_admin.storage_check as module
        from hue_admin.acceptance_watchdog import _live_group
        lock={'schema':1,'toolVersion':'1.47.3','files':{'e2fsck':'a'*64,'ld.so.1':'b'*64},'links':{}}
        created=[];original=subprocess.Popen
        def interrupted(*args,**kwargs):
            process=original(*args,**kwargs);created.append(process)
            os.kill(os.getpid(),signal.SIGINT)
            return process
        with tempfile.TemporaryDirectory() as directory,tempfile.TemporaryFile() as target:
            runtime=Runtime(directory,lock)
            try:
                with patch.object(runtime,'command',return_value=[sys.executable,'-c','import time;time.sleep(30)']), \
                     patch.object(module.subprocess,'Popen',side_effect=interrupted):
                    with self.assertRaises(KeyboardInterrupt):runtime.run(target.fileno(),3)
                self.assertEqual(len(created),1)
                self.assertIsNotNone(created[0].returncode,'child survived cancelled launch')
                self.assertFalse(_live_group(created[0].pid))
            finally:
                for process in created:
                    if process.poll() is None:
                        os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)


if __name__=='__main__':unittest.main()
