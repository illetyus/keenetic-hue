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

    def test_verified_probe_enforces_tcp_peer_and_process_identity(self):
        with patch('hue_admin.health.http.client.HTTPConnection') as factory:
            conn=factory.return_value
            conn.getresponse.return_value.status=200
            conn.getresponse.return_value.read.return_value=b'ready'
            with patch('hue_admin.health.verify_tcp_peer',return_value=False), \
                 patch('hue_admin.health.verify_process_identity',return_value=True):
                with self.assertRaisesRegex(ValueError,'HEALTH_PEER'):
                    health.verified_probe(18790,'/ready',625,expected_uid=61000,expected_gid=61000)
            conn.request.assert_not_called()
            with patch('hue_admin.health.verify_tcp_peer',return_value=True), \
                 patch('hue_admin.health.verify_process_identity',return_value=False):
                with self.assertRaisesRegex(ValueError,'HEALTH_PEER'):
                    health.verified_probe(18790,'/ready',625,expected_uid=61000,expected_gid=61000)
            conn.request.assert_not_called()
            with patch('hue_admin.health.verify_tcp_peer',return_value=True), \
                 patch('hue_admin.health.verify_process_identity',return_value=True):
                with self.assertRaisesRegex(ValueError,'HEALTH_PEER'):
                    health.verified_probe(18790,'/ready',625,peer_guard=lambda:False,expected_uid=61000,expected_gid=61000)
                self.assertEqual(health.verified_probe(18790,'/ready',625,expected_uid=61000,expected_gid=61000),(200,{}))
            conn.request.assert_called_once_with('GET','/ready',headers={})
            for bad in (-1,True,'0',2147483648):
                with self.assertRaisesRegex(ValueError,'HEALTH_IDENTITY'):
                    health.verified_probe(18790,'/ready',625,expected_uid=bad,expected_gid=0)

    def test_legacy_status_tunnel_probe_fails_closed_without_running_verified_peer(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);agent_dir=base/'agent';tunnel_dir=base/'tunnel'
            agent_dir.mkdir();tunnel_dir.mkdir()
            (agent_dir/'agent.pid').write_text('605\n',encoding='utf-8')
            (tunnel_dir/'tunnel.pid').write_text('625\n',encoding='utf-8')
            with patch.object(health,'AGENT',agent_dir), \
                 patch.object(health,'TUNNEL',tunnel_dir), \
                 patch.object(health,'private_read',return_value='{"token_file":"'+(agent_dir/'api.token').as_posix()+'"}'), \
                 patch.object(health,'authenticated_probe',return_value=(200,{'status':'verified'})), \
                 patch.object(health,'probe',side_effect=AssertionError('plain probe forbidden')):
                with patch.object(health,'process_state',side_effect=['RUNNING','PROCESS_ABSENT','RUNNING']):
                    stopped=health.status(live=True)
                self.assertFalse(stopped['healthy'])
                self.assertEqual(stopped['components']['tunnel']['reason'],'TUNNEL_UNREACHABLE')
                with patch.object(health,'process_state',return_value='RUNNING'), \
                     patch.object(health,'verified_probe',return_value=(200,{})) as mock_vp:
                    ok=health.status(live=True)
                self.assertTrue(ok['healthy'])
                self.assertEqual(mock_vp.call_args.args[:3],(18790,'/ready',625))
