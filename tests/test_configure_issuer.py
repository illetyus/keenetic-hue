from contextlib import nullcontext
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hue_admin.configure_issuer import configure_issuer


class ConfigureIssuerTests(unittest.TestCase):
    def test_failed_remote_verification_does_not_replace_local_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'secrets').mkdir();p=root/'secrets/setup-issuer.token'
            p.write_bytes(b'old');p.chmod(0o600)
            with patch('hue_admin.configure_issuer.management_lock',return_value=nullcontext()),patch('hue_admin.configure_issuer.request_setup_code',side_effect=RuntimeError('failed')):
                with self.assertRaises(RuntimeError):configure_issuer('https://relay.test','a'*64,root)
            self.assertEqual(p.read_bytes(),b'old')
            self.assertFalse((root/'config').exists())

    def test_invalid_key_never_reaches_network(self):
        with patch('hue_admin.configure_issuer.request_setup_code') as request:
            with self.assertRaises(ValueError):configure_issuer('https://relay.test','invalid')
            request.assert_not_called()

    def test_verified_key_writes_only_private_fixed_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch('hue_admin.configure_issuer.management_lock',return_value=nullcontext()) as lock,patch('hue_admin.configure_issuer.request_setup_code',return_value={'code':'B'*26,'expiresIn':600}):
                result=configure_issuer('https://relay.test','a'*64,root)
            lock.assert_called_once_with(root)
            self.assertEqual((root/'secrets/setup-issuer.token').read_text(),'a'*64)
            self.assertNotIn('a'*64,str(result));self.assertNotIn('B'*26,str(result))

    def test_linux_root_rejects_non_root_owned_issuer_directory(self):
        import os, stat
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'config').mkdir();(root/'secrets').mkdir()
            def fake_stat(p, *args, **kwargs):
                if p.name in ('config','secrets'):
                    return SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=1000)
                return SimpleNamespace(st_mode=stat.S_IFDIR|0o700,st_uid=0)
            with patch('hue_admin.configure_issuer.management_lock',return_value=nullcontext()), \
                 patch('hue_admin.configure_issuer.request_setup_code',return_value={'code':'B'*26,'expiresIn':600}), \
                 patch('hue_admin.configure_issuer.sys.platform','linux'), \
                 patch('os.geteuid',return_value=0,create=True), \
                 patch.object(Path,'stat',fake_stat):
                with self.assertRaisesRegex(ValueError,'ISSUER_PRIVATE_DIRECTORY'):
                    configure_issuer('https://relay.test','a'*64,root)
