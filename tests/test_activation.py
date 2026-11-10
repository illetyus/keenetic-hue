import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release,verify_release
from hue_admin.installation import install_candidate
from hue_admin.activation import activate_candidate,recover_activation,rollback


class PowerCut(BaseException):pass


class ActivationTests(unittest.TestCase):
    def fixture(self,tmp,new_schema=1):
        root=Path(tmp)/'system';key=Ed25519PrivateKey.generate()
        public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        runtime=('mips-3.4','cp313',{'bumble':'0.0.236'})
        for version,schema in (('0.1.0',1),('0.2.0',new_schema)):
            archive=Path(tmp)/(version+'.huerel')
            build_release({'agent/hue_agent/__init__.py':version.encode(),
                           'metadata/state-schema.json':json.dumps({'schema':1,'read':[1],'write':schema}).encode()},archive,key,version,*runtime,release_sequence=1 if version=='0.1.0' else 2)
            release=verify_release(archive,public,*runtime)
            install_candidate(release,root,'fresh')
        (root/'active.json').write_text('{"schema":1,"releaseId":"0.1.0"}');(root/'active.json').chmod(0o600)
        (root/'state/schema.json').write_text('{"schema":1}');(root/'state/schema.json').chmod(0o600)
        return root,public,runtime

    def current(self,root):return json.loads((root/'active.json').read_text())['releaseId']

    def test_success_and_explicit_rollback_keep_two_good_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,key,runtime=self.fixture(tmp);events=[]
            health=lambda:{'healthy':True,'releaseId':self.current(root)}
            activate_candidate(root,'0.2.0',key,runtime,lambda:events.append('stop') or True,lambda:events.append('start') or True,health)
            self.assertEqual(self.current(root),'0.2.0');self.assertEqual(events,['stop','start'])
            rollback(root,key,runtime,lambda:True,lambda:True,health)
            self.assertEqual(self.current(root),'0.1.0')
            self.assertTrue((root/'releases/0.2.0').is_dir())

    def test_healthy_old_owner_cannot_accept_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,key,runtime=self.fixture(tmp)
            with self.assertRaises(RuntimeError):
                activate_candidate(root,'0.2.0',key,runtime,lambda:True,lambda:True,lambda:{'healthy':True,'releaseId':'0.1.0'})
            self.assertEqual(self.current(root),'0.1.0')

    def test_state_schema_incompatibility_fails_before_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,key,runtime=self.fixture(tmp,2);calls=[]
            with self.assertRaises(ValueError):
                activate_candidate(root,'0.2.0',key,runtime,lambda:calls.append('stop'),lambda:True,lambda:{})
            self.assertEqual(calls,[]);self.assertEqual(self.current(root),'0.1.0')

    def test_power_cut_recovers_previous_selection(self):
        for point in ('prepared','stopped','selected','started'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as tmp:
                root,key,runtime=self.fixture(tmp)
                def cut(phase):
                    if phase==point:raise PowerCut()
                with self.assertRaises(PowerCut):
                    activate_candidate(root,'0.2.0',key,runtime,lambda:True,lambda:True,lambda:{'healthy':True,'releaseId':self.current(root)},cut)
                recover_activation(root,key,runtime,lambda:True,lambda:True)
                self.assertEqual(self.current(root),'0.1.0')

    def test_stop_failure_never_selects_new_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,key,runtime=self.fixture(tmp)
            with self.assertRaises(RuntimeError):activate_candidate(root,'0.2.0',key,runtime,lambda:False,lambda:True,lambda:{})
            self.assertEqual(self.current(root),'0.1.0')

    def test_fresh_install_activation_failure_recovers_without_deadlock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,key,runtime=self.fixture(tmp)
            (root/'active.json').unlink()
            def cut(phase):
                if phase=='started':raise PowerCut()
            with self.assertRaises(PowerCut):
                activate_candidate(root,'0.1.0',key,runtime,lambda:True,lambda:True,lambda:{'healthy':True,'releaseId':'0.1.0'},cut)
            start_calls = []
            res = recover_activation(root,key,runtime,lambda:True,lambda:start_calls.append(1) or True)
            self.assertTrue(res.get('recoveryComplete'))
            self.assertEqual(start_calls, [])
            self.assertFalse((root/'active.json').exists())
            receipt = json.loads((root/'state/activation.json').read_text())
            self.assertEqual(receipt['phase'], 'rolled_back')
