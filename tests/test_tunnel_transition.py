"""Ownership must follow authenticated release layout during update/rollback."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release,verify_release
from hue_admin.installation import install_candidate
from hue_admin import service_manager as manager
from hue_admin.privileges import runtime_paths,tunnel_arguments


class TunnelTransitionTests(unittest.TestCase):
    def fixture(self,directory):
        root=Path(directory)/'system';key=Ed25519PrivateKey.generate()
        public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        runtime=('mips-3.4','cp313',{'bumble':'0.0.236'})
        for sequence in (4,5,10):
            version=f'0.2.0-rc.{sequence}'
            files={'bin/cloudflared':b'\x7fELFfixture'}
            if sequence>=5:files['manager/hue_admin/privileges.py']=b'# signed restricted contract'
            if sequence==10:
                files['metadata/runtime-policy.json']=b'{"schema":1,"agent":"dedicated","tunnel":"dedicated","childIdentity":true,"kernel":false}'
            archive=Path(directory)/(version+'.huerel')
            build_release(files,archive,key,version,*runtime,release_sequence=sequence)
            install_candidate(verify_release(archive,public,*runtime),root,'fresh')
        profile=root/'state/service-identities.json'
        profile.write_text(json.dumps({'hue-tunnel':{'schema':1,'uid':61000,'gid':61000},'hue-agent':{'schema':1,'uid':61001,'gid':61001}}));profile.chmod(0o600)
        paths=runtime_paths(root,'0.2.0-rc.5');paths['binary'].parent.mkdir(parents=True)
        paths['binary'].write_bytes(b'\x7fELFfixture')
        return root,paths

    def owns(self,root,version,args,uid,bounding='0',effective='0'):
        record={'releaseId':version,'bootId':'fixture','ownerFd':7}
        status=f'Uid: {uid} {uid} {uid} {uid}\nGid: {uid} {uid} {uid} {uid}\nCapEff: {effective}\nCapPrm: 0\nCapBnd: {bounding}\n'
        with patch.object(manager,'process',return_value=('123',[v.encode() for v in args])),patch.object(manager,'boot_id',return_value='fixture'),patch.object(Path,'read_text',return_value=status):
            return manager.child_owned('tunnel',root,record,999)

    def test_transition_manager_accepts_both_correct_signed_layouts(self):
        with tempfile.TemporaryDirectory() as directory:
            root,paths=self.fixture(directory)
            old=[str(root/'releases/0.2.0-rc.4/bin/cloudflared'),*manager.tunnel_arguments(root)]
            new=[str(paths['binary']),*tunnel_arguments(paths['token'],paths['log'])]
            self.assertTrue(self.owns(root,'0.2.0-rc.4',old,0))
            self.assertTrue(self.owns(root,'0.2.0-rc.5',new,61000))
            self.assertFalse(self.owns(root,'0.2.0-rc.5',old,0))
            self.assertFalse(self.owns(root,'0.2.0-rc.4',old,61000))

    def test_unsigned_contract_change_cannot_enable_root_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root,paths=self.fixture(directory)
            manifest=root/'releases/0.2.0-rc.5/verified-manifest.json'
            value=json.loads(manifest.read_text());value['files'].pop('manager/hue_admin/privileges.py');manifest.write_text(json.dumps(value))
            args=[str(root/'releases/0.2.0-rc.5/bin/cloudflared'),*manager.tunnel_arguments(root)]
            self.assertFalse(self.owns(root,'0.2.0-rc.5',args,0))

    def test_only_signed_rc5_legacy_tunnel_can_retain_bounding_set(self):
        with tempfile.TemporaryDirectory() as directory:
            root,old=self.fixture(directory)
            new=runtime_paths(root,'0.2.0-rc.10')
            new['binary'].parent.mkdir(parents=True)
            new['binary'].write_bytes(b'\x7fELFfixture')
            child_record=root/'run/tunnel-child.json'
            child_record.parent.mkdir(exist_ok=True)
            child_record.write_text(json.dumps({'schema':1,'pid':999,'releaseId':'0.2.0-rc.10',
                                                'bootId':'fixture','startTicks':'123'}))
            child_record.chmod(0o600)
            old_args=[str(old['binary']),*tunnel_arguments(old['token'],old['log'])]
            new_args=[str(new['binary']),*tunnel_arguments(new['token'],new['log'])]
            self.assertTrue(self.owns(root,'0.2.0-rc.5',old_args,61000,bounding='3fffffffff'))
            self.assertFalse(self.owns(root,'0.2.0-rc.5',old_args,61000,bounding='3fffffffff',effective='1'))
            self.assertFalse(self.owns(root,'0.2.0-rc.10',new_args,61000,bounding='3fffffffff'))
            self.assertTrue(self.owns(root,'0.2.0-rc.10',new_args,61000))
