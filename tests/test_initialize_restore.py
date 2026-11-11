import json
import sqlite3
import tempfile
import unittest
import io
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from hue_admin.backup import seal
from hue_admin.migration import LegacyLayout
from hue_admin.service_manager import agent_paths
from hue_admin.initialize_restore import initialize, recover_if_needed

class PowerCut(BaseException):pass

class InitializeRestoreTests(unittest.TestCase):
    def test_cli_initialize_dispatches_explicit_release_and_hides_password(self):
        from hue_admin.cli import main
        output=io.StringIO()
        with patch('sys.stdin.isatty',return_value=True),patch('sys.stderr.isatty',return_value=True),patch('hue_admin.cli.getpass',return_value='private-pass'),patch('hue_admin.cli.initialize_archive_action',return_value={'verified':True}) as action,redirect_stdout(output):
            self.assertEqual(main(['restore','fixture.huebak','--initialize','--release-id','0.2.0','--dry-run']),0)
        action.assert_called_once_with('fixture.huebak','private-pass','0.2.0',True)
        self.assertNotIn('private-pass',output.getvalue())

    def fixture(self,tmp):
        root=Path(tmp)/'system'
        for name in ('config','secrets','state','run','logs','backups'):(root/name).mkdir(mode=0o700,parents=True)
        signing=Ed25519PrivateKey.generate();key=signing.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        (root/'config/release-trust.pub').write_bytes(key);(root/'config/release-trust.pub').chmod(0o600)
        # The lifecycle tests stub platform admission, but policy selection
        # still authenticates a real signed legacy contract.
        from hue_admin.releases import build_release
        release=Path(tmp)/'legacy-release.huerel'
        build_release({'metadata/state-schema.json':b'{"schema":1,"read":[1],"write":1}'},
                      release,signing,'0.2.0','mips-3.4','cp313',{'bumble':'0.0.236'})
        manifest=root/'releases/0.2.0/verified-manifest.json';manifest.parent.mkdir(parents=True,mode=0o700)
        with zipfile.ZipFile(release) as bundle:manifest.write_bytes(bundle.read('manifest.json'))
        manifest.chmod(0o600)
        cfg=agent_paths(root);cfg.update(transport='usb:fixture',address='lamp',controller_public_address='adapter',port=8787,
                                       cloud={'enabled':False},tunnel={'enabled':True,'port':8788,'token_file':str(root/'secrets/device.token')})
        files={'config/agent.json':json.dumps(cfg).encode(),'config/device.json':b'{"name":"fixture"}',
               'config/management.json':b'{"publicOrigin":"https://fixture.invalid"}','config/release-trust.pub':key,
               'secrets/bond.json':b'{"lamp":{}}'}
        for name in ('api','device','tunnel','setup-issuer'):files['secrets/'+name+'.token']=b'f'*64
        dbfile=Path(tmp)/'journal';db=sqlite3.connect(dbfile)
        db.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
        db.execute('CREATE TABLE sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)');db.commit();db.close()
        files['state/commands.sqlite']=dbfile.read_bytes()
        archive=Path(tmp)/'private.huebak';archive.write_bytes(seal(files,'fixture-password',{'adapter':'adapter','lamp':'lamp'}));archive.chmod(0o600)
        layout=LegacyLayout(*(Path(tmp)/name for name in ('old-agent','old-tunnel','old-ble','S99','S98')))
        calls=[]
        service=lambda verb:calls.append(verb) or True
        slot=Path(tmp)/'slot';slot.mkdir();(slot/'metadata').mkdir()
        p=slot/'metadata/state-schema.json';p.write_bytes(b'{"schema":1,"read":[1],"write":1}');p.chmod(0o600)
        return root,key,files,archive,layout,calls,service,slot

    def test_blank_install_recovers_config_bond_cloud_identity_and_signed_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,key,files,archive,layout,calls,service,slot=self.fixture(tmp)
            with patch('hue_admin.initialize_restore.verify_slot',return_value=slot):
                dry=initialize(root,archive,'fixture-password','0.2.0',key,('fixture',),service,lambda:None,True,legacy=layout)
                self.assertFalse((root/'config/agent.json').exists());self.assertEqual(calls,[])
                result=initialize(root,archive,'fixture-password','0.2.0',key,('fixture',),service,lambda:{'healthy':True,'releaseId':'0.2.0'},legacy=layout)
            self.assertTrue(dry['verified']);self.assertTrue(result['initialized'])
            self.assertTrue(all((root/n).read_bytes()==d for n,d in files.items()))
            self.assertEqual(calls,['start'])

    def test_all_interruptions_quarantine_partial_files_and_leave_anchor(self):
        for point in ('prepared','write:config/agent.json','written','selected','started'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as tmp:
                root,key,files,archive,layout,calls,service,slot=self.fixture(tmp)
                def cut(phase):
                    if phase==point:raise PowerCut()
                with patch('hue_admin.initialize_restore.verify_slot',return_value=slot),self.assertRaises(PowerCut):
                    initialize(root,archive,'fixture-password','0.2.0',key,('fixture',),service,lambda:None,legacy=layout,failpoint=cut)
                recover_if_needed(root,service)
                self.assertFalse((root/'active.json').exists());self.assertFalse((root/'config/agent.json').exists())
                self.assertEqual((root/'config/release-trust.pub').read_bytes(),key)
                self.assertEqual(calls[-1],'stop')

    def test_existing_install_bad_password_wrong_anchor_and_bad_database_do_not_start(self):
        for reason in ('existing','password','anchor','database'):
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as tmp:
                root,key,files,archive,layout,calls,service,slot=self.fixture(tmp)
                if reason=='existing':(root/'secrets/api.token').write_bytes(b'existing')
                if reason=='anchor':files['config/release-trust.pub']=b'q'*32
                if reason=='database':files['state/commands.sqlite']=b'not sqlite'
                if reason in ('anchor','database'):
                    archive.write_bytes(seal(files,'fixture-password',{'adapter':'adapter','lamp':'lamp'}))
                with patch('hue_admin.initialize_restore.verify_slot',return_value=slot),self.assertRaises(Exception):
                    initialize(root,archive,'wrong' if reason=='password' else 'fixture-password','0.2.0',key,('fixture',),service,lambda:None,legacy=layout)
                self.assertEqual(calls,[]);self.assertFalse((root/'active.json').exists())

    def test_long_tunnel_token_up_to_4096_is_accepted_and_over_4096_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,key,files,archive,layout,calls,service,slot=self.fixture(tmp)
            files['secrets/tunnel.token']=b't'*512
            archive.write_bytes(seal(files,'fixture-password',{'adapter':'adapter','lamp':'lamp'}))
            with patch('hue_admin.initialize_restore.verify_slot',return_value=slot):
                dry=initialize(root,archive,'fixture-password','0.2.0',key,('fixture',),service,lambda:None,True,legacy=layout)
            self.assertTrue(dry['verified'])
            files['secrets/tunnel.token']=b't'*4097
            archive.write_bytes(seal(files,'fixture-password',{'adapter':'adapter','lamp':'lamp'}))
            with patch('hue_admin.initialize_restore.verify_slot',return_value=slot),self.assertRaisesRegex(ValueError,'INITIALIZE_TOKEN'):
                initialize(root,archive,'fixture-password','0.2.0',key,('fixture',),service,lambda:None,True,legacy=layout)
