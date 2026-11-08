import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release

spec=importlib.util.spec_from_file_location('standalone_install',Path(__file__).resolve().parents[1]/'deploy/install_release.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)


class StandaloneInstallTests(unittest.TestCase):
    def test_account_recovery_hint_never_echoes_untrusted_exception_text(self):
        text=installer.install_error_message(ValueError('ACCOUNT_UNPROFILED_NAME'))
        self.assertIn('ACCOUNT_UNPROFILED_NAME',text)
        self.assertIn('yeniden',text)
        secret='FAKE_PRIVATE_EXCEPTION_VALUE'
        self.assertNotIn(secret,installer.install_error_message(ValueError(secret)))

    def test_verified_manager_is_loaded_only_after_pinned_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
            archive=base/'fixture.huerel'
            files={'manager/hue_admin/__init__.py':b'raise AssertionError("must not execute while checking")',
                   'manager/hue_admin/cli.py':b'# fixture'}
            build_release(files,archive,key,'0.2.0','mips-3.4','cp313',{'bumble':'0.0.236'})
            with installer.authenticated_manager(archive,public) as manager:
                self.assertEqual((manager/'hue_admin/cli.py').read_bytes(),b'# fixture')
            self.assertFalse(manager.exists())
            wrong=Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
            with self.assertRaises(Exception):
                with installer.authenticated_manager(archive,wrong):self.fail('untrusted code admitted')

    def test_modified_manager_and_duplicate_manifest_are_rejected(self):
        for duplicate in (False,True):
            with self.subTest(duplicate=duplicate),tempfile.TemporaryDirectory() as tmp:
                archive=Path(tmp)/'fixture.huerel';key=Ed25519PrivateKey.generate()
                public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
                files={'manager/hue_admin/__init__.py':b'# fixture','manager/hue_admin/cli.py':b'# fixture'}
                build_release(files,archive,key,'0.2.0','mips-3.4','cp313',{'bumble':'0.0.236'})
                with zipfile.ZipFile(archive,'a') as z:
                    if duplicate:z.writestr('manifest.json',z.read('manifest.json'))
                    else:z.writestr('manager/hue_admin/cli.py',b'tampered')
                with self.assertRaises(Exception):
                    with installer.authenticated_manager(archive,public):self.fail('modified code admitted')
