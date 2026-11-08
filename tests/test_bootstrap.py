import json
import tempfile
import unittest
from unittest.mock import patch, Mock
import sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release,verify_release
from hue_admin.installation import install_candidate
from hue_admin.bootstrap import selected_manager, run


class BootstrapTests(unittest.TestCase):
    runtime=('mips-3.4','cp313',{'bumble':'0.0.236'})
    def fixture(self,tmp):
        root=Path(tmp)/'system';key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        archive=Path(tmp)/'release.huerel'
        build_release({'manager/hue_admin/__init__.py':b'# fixture','manager/hue_admin/cli.py':b'# fixture'},archive,key,'0.2.0',*self.runtime)
        install_candidate(verify_release(archive,public,*self.runtime),root,'fresh')
        (root/'active.json').write_text('{"schema":1,"releaseId":"0.2.0"}');(root/'active.json').chmod(0o600)
        return root

    def test_manager_is_selected_only_after_signature_and_code_hash_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.fixture(tmp)
            self.assertEqual(selected_manager(root,lambda _:self.runtime),root/'releases/0.2.0/manager')
            (root/'releases/0.2.0/manager/hue_admin/cli.py').write_bytes(b'changed')
            with self.assertRaises(ValueError):selected_manager(root,lambda _:self.runtime)

    def test_unsigned_extra_python_module_cannot_be_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.fixture(tmp)
            (root/'releases/0.2.0/manager/hue_admin/extra.py').write_bytes(b'changed')
            with self.assertRaises(ValueError):selected_manager(root,lambda _:self.runtime)

    def test_bad_pointer_never_falls_back_to_legacy_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.fixture(tmp);(root/'active.json').write_text('{"schema":1,"releaseId":"../../bad"}')
            with self.assertRaises(ValueError):selected_manager(root,lambda _:self.runtime)

    def test_update_uses_staged_stable_transition_manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.fixture(tmp);cli=Mock();cli.main.return_value=0
            before=list(sys.path)
            try:
                with patch('hue_admin.bootstrap.selected_manager',side_effect=AssertionError('OLD_RELEASE_MANAGER')),patch('hue_admin.bootstrap.importlib.import_module',return_value=cli):
                    self.assertEqual(run(root,['update','candidate.huerel']),0)
                self.assertEqual(sys.path[0],str(root/'manager'))
                cli.main.assert_called_once_with(['update','candidate.huerel'])
            finally:sys.path[:]=before
