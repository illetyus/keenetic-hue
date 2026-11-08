import unittest
from unittest.mock import patch,Mock
import stat
import struct
from types import SimpleNamespace
from hue_admin import health

class HealthPeerTests(unittest.TestCase):
    def test_unverified_peer_never_loads_token(self):
        with patch('hue_admin.health.http.client.HTTPConnection') as connection:
            token=__import__('unittest.mock',fromlist=['Mock']).Mock(return_value='dummy')
            with patch('hue_admin.health.verify_tcp_peer',return_value=False,create=True):
                with self.assertRaises(ValueError):
                    health.authenticated_probe(8787,'/v1/state',token,123)
            token.assert_not_called()

    def probe_fixture(self,uid=61001,gid=61001,pid=999,guard=True):
        path=Mock();path.exists.return_value=True;path.is_symlink.return_value=False
        path.lstat.return_value=SimpleNamespace(st_mode=stat.S_IFSOCK|0o600,st_uid=61001,st_gid=61001)
        directory=SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=61001,st_gid=61001)
        path.parent.lstat.return_value=directory;path.parent.stat.return_value=directory
        token=Mock(return_value='fixture-token')
        with patch('hue_admin.health.http.client.HTTPConnection') as factory,patch('hue_admin.health.socket.socket') as sockets,patch('hue_admin.health.socket.SO_PEERCRED',17,create=True),patch('hue_admin.health.socket.AF_UNIX',1,create=True):
            connection=factory.return_value;connection.getresponse.return_value.status=200
            connection.getresponse.return_value.read.return_value=b'{"status":"verified"}'
            sockets.return_value.getsockopt.return_value=struct.pack('3i',pid,uid,gid)
            try:
                result=health.authenticated_probe(8787,'/v1/state',token,999,path,
                    lambda:guard,expected_uid=61001,expected_gid=61001)
            except ValueError:
                token.assert_not_called()
                raise
        return result,token

    def test_expected_service_uid_can_be_different_from_root_checker(self):
        result,token=self.probe_fixture()
        self.assertEqual(result,(200,{'status':'verified'}));token.assert_called_once()

    def test_other_uid_gid_pid_or_stale_owner_never_receives_token(self):
        for values in ({'uid':61000},{'gid':61000},{'pid':998},{'guard':False}):
            with self.subTest(values=values),self.assertRaises(ValueError):self.probe_fixture(**values)

    def test_tcp_identity_requires_all_ids_match(self):
        rows='Uid: 61001 61001 61001 61001\nGid: 61001 61001 61001 61001\n'
        with patch('hue_admin.health.Path.read_text',return_value=rows):
            self.assertTrue(health.verify_process_identity(999,61001,61001))
        with patch('hue_admin.health.Path.read_text',return_value=rows.replace('61001 61001 61001 61001','61001 0 61001 61001',1)):
            self.assertFalse(health.verify_process_identity(999,61001,61001))
