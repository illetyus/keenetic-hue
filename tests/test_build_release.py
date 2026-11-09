import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import build_release
from deploy.build_release import collect


class BuildReleaseTests(unittest.TestCase):
    def fixture(self,root):
        root=Path(root)
        for package in ('hue_agent','hue_admin'):
            (root/package).mkdir();(root/package/'__init__.py').write_bytes(b'# code')
            (root/package/'private.token').write_bytes(b'FAKE_SECRET_MUST_NOT_SHIP')
        deploy=root/'deploy';deploy.mkdir()
        for name in ('huectl','huectl-entry.py','S97keenetic-hue','hue-supervise.sh','component-child.py','hue-usb-event','acceptance-entry.py'):
            (deploy/name).write_bytes(b'# code')
        binary=root/'fake-binary';binary.write_bytes(b'fixture-binary')
        lock={'schema':1,'architecture':'mips-3.4','pythonAbi':'cp313','pythonDependencies':{'bumble':'0.0.236'},'nativeDependencies':{},
              'tunnelBinary':{'size':binary.stat().st_size,'sha256':hashlib.sha256(binary.read_bytes()).hexdigest()}}
        (root/'requirements-router.lock.json').write_text(json.dumps(lock))
        (root/'.env').write_bytes(b'FAKE_SECRET_MUST_NOT_SHIP')
        return binary

    def test_collect_excludes_private_files_and_deploy_extras(self):
        with tempfile.TemporaryDirectory() as root:
            binary=self.fixture(root)
            (Path(root)/'deploy/unlisted.py').write_bytes(b'FAKE_SECRET_MUST_NOT_SHIP')
            with patch('deploy.build_release.ROOT',Path(root)):files,lock=collect(binary)
            self.assertNotIn(b'FAKE_SECRET_MUST_NOT_SHIP',b''.join(files.values()))
            self.assertFalse(any('unlisted' in name or 'private.token' in name for name in files))
            self.assertIn('bin/cloudflared',files)
            self.assertIn('service/hue-usb-event',files)
            self.assertIn('service/acceptance-entry.py',files)

    def test_unpinned_binary_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            binary=self.fixture(root);binary.write_bytes(b'different-bytes')
            with patch('deploy.build_release.ROOT',Path(root)),self.assertRaises(ValueError):collect(binary)

    def test_private_key_pem_in_tunnel_binary_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            binary=self.fixture(root)
            payload=b'-----BEGIN EC PRIVATE KEY-----\\nfixture\\n-----END EC PRIVATE KEY-----\\n'
            binary.write_bytes(payload)
            lock_path=Path(root)/'requirements-router.lock.json'
            lock=json.loads(lock_path.read_text())
            lock['tunnelBinary']={'size':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
            lock_path.write_text(json.dumps(lock))
            with patch('deploy.build_release.ROOT',Path(root)),self.assertRaisesRegex(ValueError,'BUILD_PRIVATE_KEY_MATERIAL'):
                collect(binary)

    def test_new_release_declares_restricted_runtime_contract(self):
        with tempfile.TemporaryDirectory() as root:
            binary=self.fixture(root)
            with patch('deploy.build_release.ROOT',Path(root)):files,_=collect(binary)
            self.assertEqual(json.loads(files['metadata/runtime-policy.json']),{
                'schema':1,'agent':'dedicated','tunnel':'dedicated',
                'childIdentity':True,'kernel':False})

    def test_shell_text_is_normalized_for_windows_git_checkouts(self):
        with tempfile.TemporaryDirectory() as root:
            binary=self.fixture(root)
            (Path(root)/'deploy/huectl').write_bytes(b'#!/bin/sh\r\nexit 0\r\n')
            with patch('deploy.build_release.ROOT',Path(root)):files,_=collect(binary)
            self.assertEqual(files['service/huectl'],b'#!/bin/sh\nexit 0\n')

    def test_rejects_unlisted_python_module_in_package_dir(self):
        with tempfile.TemporaryDirectory() as root:
            binary=self.fixture(root)
            (Path(root)/'hue_agent'/'scratch_debug.py').write_text('x=1\n',encoding='utf-8')
            with patch.object(build_release,'ROOT',Path(root)):
                with self.assertRaisesRegex(ValueError,'BUILD_UNLISTED_SOURCE'):
                    collect(binary)

    def test_package_module_allowlist_matches_repository_sources(self):
        self.assertTrue(hasattr(build_release,'PACKAGE_MODULES'))
        for package,allowed in build_release.PACKAGE_MODULES.items():
            actual={path.name for path in (build_release.ROOT/package).glob('*.py')}
            self.assertEqual(actual,set(allowed))
