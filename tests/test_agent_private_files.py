from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hue_agent.private_files import private_file,private_health_path


class AgentPrivateFileTests(unittest.TestCase):
    def check(self,uid,gid,mode,owner,group,links=1):
        info=SimpleNamespace(st_mode=stat.S_IFREG|mode,st_uid=owner,st_gid=group,st_nlink=links)
        with patch('hue_agent.private_files.os.geteuid',return_value=uid,create=True),patch('hue_agent.private_files.os.getegid',return_value=gid,create=True),patch.object(Path,'lstat',return_value=info):
            return private_file('/fixed/file')

    def test_legacy_root_and_owned_private_files_are_supported(self):
        self.assertEqual(self.check(0,0,0o600,0,0),Path('/fixed/file'))
        self.assertEqual(self.check(61001,61001,0o600,61001,61001),Path('/fixed/file'))

    def test_nonroot_can_read_exact_root_prepared_group_readonly_file(self):
        self.assertEqual(self.check(61001,61001,0o440,0,61001),Path('/fixed/file'))

    def test_other_group_writable_open_or_linked_files_are_rejected(self):
        for mode,owner,group,links in ((0o440,0,61000,1),(0o640,0,61001,1),
                                       (0o644,61001,61001,1),(0o600,61001,61001,2)):
            with self.subTest(mode=mode,owner=owner,group=group),self.assertRaises(ValueError):
                self.check(61001,61001,mode,owner,group,links)

    def test_health_socket_must_be_sibling_of_configured_pid(self):
        self.assertEqual(private_health_path({'pid_file':'/runtime/run/agent.pid','health_socket':'/runtime/run/health.sock'}),Path('/runtime/run/health.sock'))
        self.assertEqual(private_health_path({'pid_file':'/opt/var/lib/keenetic-hue-system/run/agent.pid'}),Path('/opt/var/lib/keenetic-hue-system/run/health.sock'))
        with self.assertRaises(ValueError):private_health_path({'pid_file':'/runtime/run/agent.pid','health_socket':'/elsewhere/health.sock'})
        self.assertIsNone(private_health_path({'pid_file':'/legacy/agent.pid'}))
