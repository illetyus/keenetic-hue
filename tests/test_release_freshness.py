import tempfile,unittest,json
from pathlib import Path
from hue_admin import releases
from hue_admin.activation import activate_candidate,rollback,release_order,recover_activation
from tests.test_activation import ActivationTests,PowerCut

class FreshnessTests(unittest.TestCase):
    def test_ordinary_activation_rejects_downgrade_after_explicit_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=ActivationTests();root,key,runtime=f.fixture(tmp)
            health=lambda:{'healthy':True,'releaseId':f.current(root)}
            activate_candidate(root,'0.2.0',key,runtime,lambda:True,lambda:True,health)
            rollback(root,key,runtime,lambda:True,lambda:True,health)
            self.assertEqual(json.loads((root/'state/release-order.json').read_text())['sequence'],2)
            raw=(root/'releases/0.1.0/verified-manifest.json').read_bytes()
            with self.assertRaisesRegex(ValueError,'DOWNGRADE'):release_order(root,raw,key,runtime)

    def test_commit_power_cut_recovers_highest_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=ActivationTests();root,key,runtime=f.fixture(tmp)
            def cut(phase):
                if phase=='committed':raise PowerCut()
            with self.assertRaises(PowerCut):
                activate_candidate(root,'0.2.0',key,runtime,lambda:True,lambda:True,lambda:{'healthy':True,'releaseId':f.current(root)},cut)
            recover_activation(root,key,runtime,lambda:True,lambda:True)
            self.assertEqual(json.loads((root/'state/release-order.json').read_text())['sequence'],2)
    def test_builder_accepts_explicit_signed_sequence(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        key=Ed25519PrivateKey.generate()
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'new.huerel'
            releases.build_release({'agent/a.py':b'x'},p,key,'0.2.0-rc.4','fixture','fixture',{'fixture':'1'},release_sequence=4)
            release=releases.verify_release(p,key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw),'fixture','fixture',{'fixture':'1'})
            self.assertEqual(json.loads(release.signed_manifest)['releaseSequence'],4)
