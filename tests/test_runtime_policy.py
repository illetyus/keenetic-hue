import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release,verify_release
from hue_admin.installation import install_candidate
from hue_admin.runtime_policy import release_policy


class RuntimePolicyTests(unittest.TestCase):
    runtime=('mips-3.4','cp313',{'bumble':'0.0.236'})
    restricted={'schema':1,'agent':'dedicated','tunnel':'dedicated','childIdentity':True,'kernel':False}

    def fixture(self,directory,files):
        key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        root=Path(directory)/'system';archive=Path(directory)/'release.huerel'
        build_release(files,archive,key,'0.2.0-rc.6',*self.runtime,release_sequence=6)
        install_candidate(verify_release(archive,public,*self.runtime),root,'fresh')
        return root

    def test_signed_legacy_contracts_remain_distinct(self):
        for files,tunnel in (({'bin/cloudflared':b'fixture'},'root'),
                             ({'manager/hue_admin/privileges.py':b'fixture'},'dedicated')):
            with self.subTest(tunnel=tunnel),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
                root=self.fixture(directory,files)
                self.assertEqual(release_policy(root,'0.2.0-rc.6'),{
                    'schema':1,'agent':'root','tunnel':tunnel,'childIdentity':False,'kernel':False})

    def test_new_explicit_policy_is_authenticated(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root=self.fixture(directory,{'metadata/runtime-policy.json':json.dumps(self.restricted).encode()})
            self.assertEqual(release_policy(root,'0.2.0-rc.6'),self.restricted)
            (root/'releases/0.2.0-rc.6/metadata/runtime-policy.json').write_text(json.dumps({**self.restricted,'agent':'root'}))
            with self.assertRaises(ValueError):release_policy(root,'0.2.0-rc.6')

    def test_new_agent_runtime_cannot_silently_use_legacy_fallback(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root=self.fixture(directory,{'manager/hue_admin/agent_runtime.py':b'fixture'})
            with self.assertRaises(ValueError):release_policy(root,'0.2.0-rc.6')

    def test_signed_but_relaxed_or_mistyped_policy_is_rejected(self):
        for change in ({'agent':'root'},{'tunnel':'root'},{'childIdentity':False},
                       {'kernel':1},{'schema':True},{'extra':'ignored'}):
            with self.subTest(change=change),tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
                policy={**self.restricted,**change}
                root=self.fixture(directory,{'metadata/runtime-policy.json':json.dumps(policy).encode()})
                with self.assertRaises(ValueError):release_policy(root,'0.2.0-rc.6')

    def test_signature_and_release_label_cannot_be_bypassed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root=self.fixture(directory,{'metadata/runtime-policy.json':json.dumps(self.restricted).encode()})
            path=root/'releases/0.2.0-rc.6/verified-manifest.json'
            manifest=json.loads(path.read_bytes());manifest['files'].pop('metadata/runtime-policy.json');path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):release_policy(root,'0.2.0-rc.6')
            with self.assertRaises(ValueError):release_policy(root,'../../outside')
