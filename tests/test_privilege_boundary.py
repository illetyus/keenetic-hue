import unittest
from hue_admin.privileges import drop_identity, tunnel_environment, process_matches_identity, tunnel_arguments
from hue_admin.privileges import load_tunnel_identity, load_service_identity, runtime_paths
import tempfile,json
from pathlib import Path

class FakeOS:
 def __init__(self,fail=False):self.uid=0;self.gid=0;self.groups=[0];self.calls=[];self.fail=fail
 def setgroups(self,g):self.calls.append('groups');self.groups=g
 def setresgid(self,a,b,c):self.calls.append('gid');self.gid=a
 def setresuid(self,a,b,c):
  self.calls.append('uid')
  if self.fail:raise PermissionError('fixture')
  self.uid=a
 def getresuid(self):return (self.uid,)*3
 def getresgid(self):return (self.gid,)*3
 def getgroups(self):return self.groups

class PrivilegeTests(unittest.TestCase):
 def test_service_identities_are_distinct_and_both_validated(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
   root=Path(directory);(root/'state').mkdir()
   profile=root/'state/service-identities.json'
   values={'hue-tunnel':{'schema':1,'uid':61000,'gid':61000},'hue-agent':{'schema':1,'uid':61001,'gid':61001}}
   profile.write_text(json.dumps(values));profile.chmod(0o600)
   self.assertEqual(load_service_identity(root,'hue-agent'),values['hue-agent'])
   for change in ({'uid':0},{'gid':61000},{'uid':61000}):
    broken={**values,'hue-agent':{**values['hue-agent'],**change}}
    profile.write_text(json.dumps(broken))
    with self.assertRaises(ValueError):load_tunnel_identity(root)
 def test_missing_profile_is_fail_closed(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
   with self.assertRaises(FileNotFoundError):load_tunnel_identity(Path(d))
 def test_root_identity_profile_is_rejected(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
   root=Path(d);(root/'state').mkdir();p=root/'state/service-identities.json';p.write_text(json.dumps({'hue-tunnel':{'schema':1,'uid':0,'gid':2001},'hue-agent':{'schema':1,'uid':2002,'gid':2002}}));p.chmod(0o600)
   with self.assertRaises(ValueError):load_tunnel_identity(root)
 def test_projection_not_in_master_secrets_tree(self):
  paths=runtime_paths(Path('/opt/var/lib/keenetic-hue-system'),'0.2.0-rc.5')
  self.assertNotIn('/keenetic-hue-system/',str(paths['token']))
  with self.assertRaises(ValueError):runtime_paths(Path('/opt/var/lib/keenetic-hue-system'),'../../root')
 def test_tunnel_environment_is_allowlisted(self):
  env=tunnel_environment('/runtime/data','/opt/etc/ssl/cert.pem')
  self.assertEqual(set(env),{'PATH','HOME','LANG','GOMAXPROCS','SSL_CERT_FILE'})
  self.assertEqual(env['HOME'],'/runtime/data')
 def test_tunnel_argv_uses_only_projected_paths(self):
  args=tunnel_arguments('/runtime/credentials/tunnel.token','/runtime/data/tunnel.log')
  self.assertEqual(args[-2:],['--token-file','/runtime/credentials/tunnel.token'])
  self.assertNotIn('--token',args)
 def test_process_all_ids_and_caps_must_match(self):
  status='Uid:\t2000 2000 2000 2000\nGid:\t2001 2001 2001 2001\nCapEff:\t0\nCapPrm:\t0\nCapBnd:\t0\n'
  self.assertTrue(process_matches_identity(status,2000,2001))
  self.assertFalse(process_matches_identity(status.replace('2000 2000 2000 2000','2000 0 2000 2000'),2000,2001))
  self.assertFalse(process_matches_identity(status.replace('CapEff:\t0','CapEff:\t1'),2000,2001))
  self.assertFalse(process_matches_identity(status.replace('CapBnd:\t0','CapBnd:\t1'),2000,2001))
  self.assertFalse(process_matches_identity(status.replace('CapBnd:\t0\n',''),2000,2001))
 def test_missing_capabilities_do_not_pass(self):
  self.assertFalse(process_matches_identity('Uid: 2000 2000 2000 2000\nGid: 2001 2001 2001 2001',2000,2001))
 def test_root_target_rejected(self):
  with self.assertRaises(ValueError):drop_identity(0,2000,FakeOS(),lambda:True,lambda:True)
 def test_groups_before_gid_before_all_uid(self):
  ops=FakeOS();drop_identity(2000,2001,ops,lambda:True,lambda:True,lambda:True)
  self.assertEqual(ops.calls,['groups','gid','uid']);self.assertEqual(ops.getresuid(),(2000,)*3)
 def test_drop_error_propagates(self):
  with self.assertRaises(PermissionError):drop_identity(2000,2001,FakeOS(True),lambda:True,lambda:True,lambda:True)
 def test_nnp_failure_rejected(self):
  with self.assertRaises(RuntimeError):drop_identity(2000,2001,FakeOS(),lambda:False,lambda:True,lambda:True)
 def test_capabilities_failure_rejected(self):
  with self.assertRaises(RuntimeError):drop_identity(2000,2001,FakeOS(),lambda:True,lambda:False,lambda:True)
if __name__=='__main__':unittest.main()
