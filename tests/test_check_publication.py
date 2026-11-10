import tempfile
from pathlib import Path
import unittest
from tools.check_publication import check


class CheckPublicationTests(unittest.TestCase):
    def test_catches_active_secrets_and_bond_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Create required files so REQUIRED_FILE_MISSING is not raised
            required = ('README.md', 'README.en.md', 'LICENSE', 'SECURITY.md', 'CONTRIBUTING.md',
                        'SUPPORT.md', 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_LICENSES.txt',
                        'deploy/storage-ram.example.json')
            for name in required:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                if name.endswith('.json'):
                    p.write_text('{"ssh_host":"","ssh_port":null,"ssh_user":"","ssh_key_path":""}', encoding='utf-8')
                else:
                    p.write_text('content', encoding='utf-8')

            # Create legitimate example
            (root / 'config.example.json').write_text('{}', encoding='utf-8')

            # Create forbidden secret files
            (root / 'secrets').mkdir()
            (root / 'secrets' / 'bond.json').write_text('{"key": "val"}', encoding='utf-8')
            (root / 'device.json').write_text('{"mac": "00:11:22"}', encoding='utf-8')
            (root / 'api.token').write_text('secret-token', encoding='utf-8')
            (root / 'commands.sqlite').write_bytes(b'SQLite format 3\x00')
            (root / 'server.pem').write_text('dummy-pem-bytes', encoding='utf-8')
            (root / '.dev.vars.local').write_text('KEY=val', encoding='utf-8')

            result = check(root)
            error_paths = {e['path'] for e in result['errors'] if e.get('code') == 'PRIVATE_OR_BINARY_ARTIFACT'}

            self.assertIn('secrets/bond.json', error_paths)
            self.assertIn('device.json', error_paths)
            self.assertIn('api.token', error_paths)
            self.assertIn('commands.sqlite', error_paths)
            self.assertIn('server.pem', error_paths)
            self.assertIn('.dev.vars.local', error_paths)
            self.assertNotIn('config.example.json', error_paths)
            self.assertNotIn('README.md', error_paths)


if __name__ == '__main__':
    unittest.main()
