import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hue_admin.installation import install_candidate
from hue_admin.kernel_authorization import authorized_release
from hue_admin.releases import build_release, verify_release


class SignedKernelAuthorizationTests(unittest.TestCase):
    def fixture(self, directory, enabled):
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        runtime = ('mips-3.4', f'cp{sys.version_info.major}{sys.version_info.minor}', {'bumble': '0.0.236'})
        archive = Path(directory) / 'release.huerel'
        policy = {'schema': 1, 'agent': 'dedicated', 'tunnel': 'dedicated',
                  'childIdentity': True, 'kernel': enabled}
        build_release({'metadata/runtime-policy.json': json.dumps(policy).encode(),
                       'agent/hue_agent/__init__.py': b'fixture'},
                      archive, key, '0.2.0-rc.7', *runtime, release_sequence=7)
        root = Path(directory) / 'system'
        install_candidate(verify_release(archive, public, *runtime), root, 'fresh')
        (root / 'active.json').write_text('{"schema":1,"releaseId":"0.2.0-rc.7"}')
        (root / 'active.json').chmod(0o600)
        return root, runtime

    def test_real_signed_enabled_and_disabled_policies(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                root, runtime = self.fixture(directory, enabled)
                with patch('hue_admin.platform.runtime_for', return_value=runtime):
                    if enabled:
                        self.assertEqual(authorized_release(root), '0.2.0-rc.7')
                    else:
                        with self.assertRaisesRegex(ValueError, 'KERNEL_POLICY_DISABLED'):
                            authorized_release(root)

    def test_modified_signed_files_or_manifest_are_rejected(self):
        for name in ('metadata/runtime-policy.json', 'agent/hue_agent/__init__.py',
                     'verified-manifest.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root, runtime = self.fixture(directory, True)
                path = root / 'releases/0.2.0-rc.7' / name
                if name == 'verified-manifest.json':
                    manifest = json.loads(path.read_bytes())
                    manifest['releaseSequence'] = 8
                    path.write_text(json.dumps(manifest))
                else:
                    path.write_bytes(path.read_bytes() + b' ')
                with patch('hue_admin.platform.runtime_for', return_value=runtime):
                    with self.assertRaises(ValueError):
                        authorized_release(root)
