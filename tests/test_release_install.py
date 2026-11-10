import tempfile
import unittest
import stat
import zipfile
from unittest.mock import patch
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release, verify_release, extract_release


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate()
        self.public=self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)

    def build(self,root,files=None):
        path=Path(root)/'release.huerel'
        build_release(files or {'agent/hue_agent/__init__.py':b'# fixture\n'},path,self.key,
                      '0.2.0','mips-3.4','cp313',{'bumble':'0.0.236'})
        return path

    def verify(self,path,public=None,arch='mips-3.4',abi='cp313',deps=None):
        return verify_release(path,public or self.public,arch,abi,deps or {'bumble':'0.0.236'})

    def test_signed_roundtrip_and_private_extract(self):
        with tempfile.TemporaryDirectory() as root:
            release=self.verify(self.build(root))
            target=Path(root)/'stage'
            extract_release(release,target)
            self.assertEqual((target/'agent/hue_agent/__init__.py').read_bytes(),b'# fixture\n')

    def test_other_key_arch_abi_or_dependency_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.build(root)
            other=Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
            for kwargs in ({'public':other},{'arch':'x86_64'},{'abi':'cp312'},{'deps':{'bumble':'0.0.1'}}):
                with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):self.verify(path,**kwargs)

    def test_unsafe_names_never_built(self):
        for name in ('../escape','/agent/file','agent/../escape','agent\\escape','config/token','secrets/bond.json','metadata/bond.json','agent/file:ads','agent/file\x00extra','agent/.env','agent/api.token','agent/CON'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as root,self.assertRaises(ValueError):
                self.build(root,{name:b'data'})

    def test_archive_change_after_verification_does_not_extract(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.build(root);release=self.verify(path)
            path.write_bytes(path.read_bytes()+b'changed')
            target=Path(root)/'stage'
            with self.assertRaises(ValueError):extract_release(release,target)
            self.assertFalse(target.exists())

    def test_existing_stage_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            release=self.verify(self.build(root));target=Path(root)/'stage';target.mkdir()
            marker=target/'marker';marker.write_bytes(b'preserve')
            with self.assertRaises(ValueError):extract_release(release,target)
            self.assertEqual(marker.read_bytes(),b'preserve')

    def rewrite(self,path,change):
        with zipfile.ZipFile(path) as archive:
            entries=[(i,archive.read(i)) for i in archive.infolist()]
        with zipfile.ZipFile(path,'w') as archive:
            for info,data in entries:
                info,data=change(info,data)
                archive.writestr(info,data)

    def test_tampered_content_and_symlink_metadata_are_refused(self):
        for attack in ('hash','symlink'):
            with self.subTest(attack=attack),tempfile.TemporaryDirectory() as root:
                path=self.build(root)
                def change(info,data):
                    if info.filename!='manifest.json':
                        if attack=='hash':data=b'changed'
                        else:info.external_attr=(stat.S_IFLNK|0o700)<<16
                    return info,data
                self.rewrite(path,change)
                with self.assertRaises(ValueError):self.verify(path)

    def test_undeclared_traversal_entry_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.build(root)
            with zipfile.ZipFile(path,'a') as archive:archive.writestr('../escape',b'bad')
            with self.assertRaises(ValueError):self.verify(path)

    def test_disk_full_removes_stage_but_preserves_source_archive(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.build(root);release=self.verify(path);before=path.read_bytes();target=Path(root)/'stage'
            with patch('hue_admin.releases.os.fsync',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):extract_release(release,target)
            self.assertFalse(target.exists());self.assertEqual(path.read_bytes(),before)

    def test_build_release_writes_deterministic_manifest_zip_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path=self.build(root)
            with zipfile.ZipFile(path) as archive:
                manifest_info=archive.getinfo('manifest.json')
                self.assertEqual(manifest_info.date_time,(1980,1,1,0,0,0))
                self.assertEqual((manifest_info.external_attr>>16)&0o777,0o600)
