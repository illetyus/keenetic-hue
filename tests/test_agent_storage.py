import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hue_admin.agent_storage import agent_runtime_paths,read_agent_private


class AgentStorageTests(unittest.TestCase):
    def metadata(self,**changes):
        return SimpleNamespace(st_mode=stat.S_IFREG|0o600,st_uid=61001,st_gid=61001,
                               st_nlink=1,st_size=7,**changes) if not changes else SimpleNamespace(**{
                                   **vars(self.metadata()),**changes})

    def test_runtime_state_is_outside_root_private_master(self):
        root=Path('/opt/var/lib/keenetic-hue-system')
        paths=agent_runtime_paths(root,'0.2.0-rc.6')
        for key in ('config','device_config','keystore','journal','token_file','pid_file','health_socket'):
            self.assertTrue(str(paths[key]).startswith(str(paths['base'])))
            self.assertNotIn('/keenetic-hue-system/',str(paths[key]))
        self.assertEqual(paths['health_socket'].parent,paths['pid_file'].parent)
        with self.assertRaises(ValueError):agent_runtime_paths(root,'../../arbitrary')

    def test_owned_regular_file_is_read_through_checked_descriptor(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path=Path(directory)/'bond.json';path.write_bytes(b'fixture')
            with patch('hue_admin.agent_storage.os.fstat',return_value=self.metadata()):
                self.assertEqual(read_agent_private(path,61001,61001),b'fixture')

    def test_wrong_owner_group_links_mode_or_type_rejected_before_read(self):
        for changes in ({'st_uid':0},{'st_gid':61000},{'st_nlink':2},
                        {'st_mode':stat.S_IFREG|0o644},{'st_mode':stat.S_IFIFO|0o600}):
            with self.subTest(changes=changes),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
                path=Path(directory)/'bond.json';path.write_bytes(b'fixture')
                with patch('hue_admin.agent_storage.os.fstat',return_value=self.metadata(**changes)),patch('hue_admin.agent_storage.os.read') as reader:
                    with self.assertRaises(ValueError):read_agent_private(path,61001,61001)
                    reader.assert_not_called()

    def test_growth_past_limit_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path=Path(directory)/'bond.json';path.write_bytes(b'fixture-too-long')
            with patch('hue_admin.agent_storage.os.fstat',return_value=self.metadata(st_size=1)):
                with self.assertRaises(ValueError):read_agent_private(path,61001,61001,limit=8)

    def test_root_identity_or_unbounded_read_is_not_allowed(self):
        for uid,gid,limit in ((0,61001,100),(61001,0,100),(True,61001,100),(61001,61001,-1),(61001,61001,2**30)):
            with self.subTest(uid=uid,gid=gid,limit=limit),patch('hue_admin.agent_storage.os.open') as opened:
                with self.assertRaises(ValueError):read_agent_private(Path('/fixed/file'),uid,gid,limit)
                opened.assert_not_called()

    def test_prune_recovery_archives_keeps_latest_two_and_ignores_unrelated(self):
        from hue_admin.agent_storage import prune_recovery_archives
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            dirs=[]
            for idx in range(4):
                d=root/f'sqlite-recovery-000{idx}'
                d.mkdir()
                (d/'receipt.json').write_text('{}')
                os.utime(d,(1000+idx,1000+idx))
                dirs.append(d)
            unrelated=root/'other-dir';unrelated.mkdir()
            plain_file=root/'sqlite-recovery-file';plain_file.write_text('keep')
            prune_recovery_archives(root,keep=2)
            self.assertFalse(dirs[0].exists());self.assertFalse(dirs[1].exists())
            self.assertTrue(dirs[2].exists());self.assertTrue(dirs[3].exists())
            self.assertTrue(unrelated.exists());self.assertTrue(plain_file.exists())
