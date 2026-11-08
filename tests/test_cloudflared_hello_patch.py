import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import patch_cloudflared_hello_cert


class CloudflaredHelloPatchTests(unittest.TestCase):
    EXPECTED_PATCHED_SOURCE_SHA256='e5762ae1904b95137c85ce0f0fa491e871385c7c2a1984b34f465a92cf1a748d'

    def fixture(self, root, content=b'upstream fixture'):
        root=Path(root)
        (root/'go.mod').write_text('module github.com/cloudflare/cloudflared\n\ngo 1.27.3\n')
        source=root/'tlsconfig'/'hello_ca.go'
        source.parent.mkdir()
        source.write_bytes(content)
        return source

    def test_patches_only_the_pinned_hello_certificate_source(self):
        with tempfile.TemporaryDirectory() as root:
            content=b'upstream fixture with helloKey and helloCRT'
            source=self.fixture(root,content)
            expected=hashlib.sha256(content).hexdigest()
            with patch.object(patch_cloudflared_hello_cert,'UPSTREAM_HELLO_SOURCE_SHA256',expected):
                result=patch_cloudflared_hello_cert.apply_hello_cert_patch(root)
            patched=source.read_bytes()
            self.assertEqual(result['sourceSha256'],expected)
            self.assertEqual(result['patchedSha256'],hashlib.sha256(patched).hexdigest())
            self.assertEqual(result['patchedSha256'],self.EXPECTED_PATCHED_SOURCE_SHA256)
            self.assertNotIn(b'PRIVATE KEY',patched)
            self.assertNotIn(b'helloKey',patched)
            self.assertIn(b'func GetHelloCertificate()',patched)

    def test_refuses_an_unexpected_upstream_source_without_editing_it(self):
        with tempfile.TemporaryDirectory() as root:
            content=b'unexpected fixture content'
            source=self.fixture(root,content)
            with patch.object(patch_cloudflared_hello_cert,'UPSTREAM_HELLO_SOURCE_SHA256','0'*64):
                with self.assertRaisesRegex(ValueError,'CLOUDFLARED_HELLO_SOURCE_MISMATCH'):
                    patch_cloudflared_hello_cert.apply_hello_cert_patch(root)
            self.assertEqual(source.read_bytes(),content)


if __name__=='__main__':
    unittest.main()
