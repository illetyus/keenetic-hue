import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release,verify_release
from hue_admin.installation import install_candidate
from hue_admin.bootstrap_install import provision_bootstrap


class PowerCut(BaseException):pass


class BootstrapInstallTests(unittest.TestCase):
    def fixture(self,tmp,usb=False,restricted=False):
        root=Path(tmp)/'system';key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        runtime=('mips-3.4','cp313',{'bumble':'0.0.236'})
        source=Path(__file__).resolve().parents[1]
        files={'manager/hue_admin/__init__.py':b'# fixture','manager/hue_admin/cli.py':b'# fixture',
               'manager/huectl-entry.py':b'# fixture entry','service/huectl':(source/'deploy/huectl').read_bytes(),
               'service/S97keenetic-hue':(source/'deploy/S97keenetic-hue').read_bytes()}
        if usb:files['service/hue-usb-event']=(source/'deploy/hue-usb-event').read_bytes()
        if restricted:files['metadata/runtime-policy.json']=b'{"schema":1,"agent":"dedicated","tunnel":"dedicated","childIdentity":true,"kernel":false}'
        archive=Path(tmp)/'release.huerel';build_release(files,archive,key,'0.2.0',*runtime)
        install_candidate(verify_release(archive,public,*runtime),root,'fresh')
        parent=Path(tmp)/'entries';parent.mkdir();cli=parent/'huectl';init=parent/'S97keenetic-hue'
        (root/'manager').mkdir(mode=0o700);(root/'manager/old.py').write_bytes(b'preserve old manager')
        token=root/'secrets/fixture.token';token.write_bytes(b'preserve secret');token.chmod(0o600)
        return root,public,runtime,cli,init

    def test_repeated_provision_keeps_secrets_and_original_manager_backup(self):
        with tempfile.TemporaryDirectory() as tmp,patch('hue_admin.bootstrap_install.management_lock',return_value=nullcontext()):
            root,key,runtime,cli,init=self.fixture(tmp)
            first=provision_bootstrap(root,'0.2.0',key,runtime,cli,init)
            again=provision_bootstrap(root,'0.2.0',key,runtime,cli,init)
            self.assertTrue(again['alreadyInstalled'])
            self.assertEqual((root/'secrets/fixture.token').read_bytes(),b'preserve secret')
            self.assertEqual((root/first['backup']/'manager/old.py').read_bytes(),b'preserve old manager')
            self.assertTrue(cli.exists() and init.exists())

    def test_each_interruption_can_be_finished_with_same_trusted_candidate(self):
        for point in ('prepared','old_saved','manager_installed','cli_installed','committed'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as tmp,patch('hue_admin.bootstrap_install.management_lock',return_value=nullcontext()):
                root,key,runtime,cli,init=self.fixture(tmp)
                def cut(phase):
                    if phase==point:raise PowerCut()
                with self.assertRaises(PowerCut):provision_bootstrap(root,'0.2.0',key,runtime,cli,init,failpoint=cut)
                provision_bootstrap(root,'0.2.0',key,runtime,cli,init)
                self.assertEqual((root/'manager/hue_admin/cli.py').read_bytes(),b'# fixture')
                self.assertTrue(cli.exists() and init.exists())
                self.assertEqual((root/'secrets/fixture.token').read_bytes(),b'preserve secret')

    def test_unrelated_existing_cli_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp,patch('hue_admin.bootstrap_install.management_lock',return_value=nullcontext()):
            root,key,runtime,cli,init=self.fixture(tmp);cli.write_bytes(b'unrelated tool')
            with self.assertRaises(ValueError):provision_bootstrap(root,'0.2.0',key,runtime,cli,init)
            self.assertEqual(cli.read_bytes(),b'unrelated tool')

    def test_signed_usb_hook_is_installed_and_resumed_with_bootstrap(self):
        for point in ('cli_installed','committed'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as tmp,patch('hue_admin.bootstrap_install.management_lock',return_value=nullcontext()):
                root,key,runtime,cli,init=self.fixture(tmp,usb=True)
                def cut(phase):
                    if phase==point:raise PowerCut()
                with self.assertRaises(PowerCut):provision_bootstrap(root,'0.2.0',key,runtime,cli,init,failpoint=cut)
                provision_bootstrap(root,'0.2.0',key,runtime,cli,init)
                hook=init.parent.parent/'ndm/usb.d/50-keenetic-hue'
                self.assertEqual(hook.read_bytes(),(root/'releases/0.2.0/service/hue-usb-event').read_bytes())

    def test_restricted_bootstrap_reserves_service_identity_before_restore(self):
        with tempfile.TemporaryDirectory() as tmp,patch('hue_admin.bootstrap_install.management_lock',return_value=nullcontext()):
            root,key,runtime,cli,init=self.fixture(tmp,restricted=True)
            with patch('hue_admin.account_store.ensure_accounts') as accounts:
                provision_bootstrap(root,'0.2.0',key,runtime,cli,init)
                accounts.assert_called_once_with(root)

    def test_committed_prior_release_allows_changed_shim_and_cleans_stage_dirs(self):
        with tempfile.TemporaryDirectory() as tmp,patch('hue_admin.bootstrap_install.management_lock',return_value=nullcontext()):
            root,key,runtime,cli,init=self.fixture(tmp)
            provision_bootstrap(root,'0.2.0',key,runtime,cli,init)
            self.assertEqual(list(root.glob('.bootstrap-stage-*')),[])
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
            signing=Ed25519PrivateKey.generate();pub=signing.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
            (root/'config/release-trust.pub').write_bytes(pub)
            source=Path(__file__).resolve().parents[1]
            for ver,huectl_bytes in (('0.2.1',b'#!/bin/sh\necho v1\n'),('0.2.2',b'#!/bin/sh\necho v2\n')):
                files={'manager/hue_admin/__init__.py':b'# fixture','manager/hue_admin/cli.py':ver.encode(),
                       'manager/huectl-entry.py':b'# entry','service/huectl':huectl_bytes,
                       'service/S97keenetic-hue':(source/'deploy/S97keenetic-hue').read_bytes()}
                archive=Path(tmp)/f'{ver}.huerel';build_release(files,archive,signing,ver,*runtime)
                install_candidate(verify_release(archive,pub,*runtime),root,'repair')
            provision_bootstrap(root,'0.2.1',pub,runtime,cli,init,replace_entries=True)
            self.assertEqual(cli.read_bytes(),b'#!/bin/sh\necho v1\n')
            provision_bootstrap(root,'0.2.2',pub,runtime,cli,init,replace_entries=False)
            self.assertEqual(cli.read_bytes(),b'#!/bin/sh\necho v2\n')
            self.assertEqual(list(root.glob('.bootstrap-stage-*')),[])

    def test_finish_if_needed_normalizes_relative_entry_paths(self):
        import os
        from hue_admin.bootstrap_install import finish_if_needed
        with tempfile.TemporaryDirectory() as tmp,patch('hue_admin.bootstrap_install.management_lock',return_value=nullcontext()):
            root,key,runtime,cli,init=self.fixture(tmp)
            def cut(phase):
                if phase=='cli_installed':raise PowerCut()
            with self.assertRaises(PowerCut):provision_bootstrap(root,'0.2.0',key,runtime,cli,init,failpoint=cut)
            old_cwd=os.getcwd()
            try:
                os.chdir(tmp)
                with patch('hue_admin.release_commands.runtime_for',return_value=runtime):
                    finish_if_needed(root,Path('entries/huectl'),Path('entries/S97keenetic-hue'))
            finally:os.chdir(old_cwd)
            self.assertTrue(init.exists())
            self.assertEqual(list(root.glob('.bootstrap-stage-*')),[])
