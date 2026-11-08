import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from hue_admin.releases import build_release,verify_release
from hue_admin.installation import install_candidate,recover_installation


class InstallationTests(unittest.TestCase):
    def release(self,root):
        key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        self.key=key
        archive=Path(root)/'fixture.huerel'
        build_release({'agent/hue_agent/__init__.py':b'fixture'},archive,key,'0.2.0','mips-3.4','cp313',{'bumble':'0.0.236'})
        return verify_release(archive,public,'mips-3.4','cp313',{'bumble':'0.0.236'})

    def test_fresh_candidate_and_repeat_preserve_private_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'system';release=self.release(tmp)
            first=install_candidate(release,root,'fresh')
            self.assertFalse(first['activated']);self.assertFalse((root/'active.json').exists())
            token=root/'secrets/test.token';token.write_bytes(b'keep');token.chmod(0o600)
            again=install_candidate(release,root,'repair')
            self.assertTrue(again['alreadyVerified']);self.assertEqual(token.read_bytes(),b'keep')

    def test_corrupted_inactive_slot_can_be_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'system';release=self.release(tmp)
            install_candidate(release,root,'fresh')
            path=root/'releases/0.2.0/agent/hue_agent/__init__.py';path.write_bytes(b'bad')
            install_candidate(release,root,'repair')
            self.assertEqual(path.read_bytes(),b'fixture')

    def test_active_corruption_requires_coordinated_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'system';release=self.release(tmp)
            install_candidate(release,root,'fresh')
            (root/'active.json').write_text('{"schema":1,"releaseId":"0.2.0"}')
            (root/'active.json').chmod(0o600)
            path=root/'releases/0.2.0/agent/hue_agent/__init__.py';path.write_bytes(b'bad')
            with self.assertRaises(ValueError):install_candidate(release,root,'repair')
            self.assertEqual(path.read_bytes(),b'bad')

    def test_power_cut_at_each_promotion_phase_is_repeatable(self):
        class PowerCut(BaseException):pass
        for point in ('prepared','staged','promoted','committed'):
            with self.subTest(point=point),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'system';release=self.release(tmp)
                def cut(phase):
                    if phase==point:raise PowerCut()
                with self.assertRaises(PowerCut):install_candidate(release,root,'fresh',failpoint=cut)
                result=install_candidate(release,root,'repair')
                self.assertFalse(result['activated'])
                self.assertEqual((root/'releases/0.2.0/agent/hue_agent/__init__.py').read_bytes(),b'fixture')
                self.assertFalse((root/'active.json').exists())

    def test_extract_failure_never_creates_final_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'system';release=self.release(tmp)
            with patch('hue_admin.installation.extract_release',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):install_candidate(release,root,'fresh')
            self.assertFalse((root/'releases/0.2.0').exists())
            self.assertFalse((root/'active.json').exists())

    def test_bad_active_pointer_is_never_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'system';release=self.release(tmp)
            install_candidate(release,root,'fresh')
            (root/'active.json').write_text('{bad')
            with self.assertRaises(ValueError):install_candidate(release,root,'repair')

    def test_same_version_different_signed_code_is_not_a_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'system';release=self.release(tmp)
            install_candidate(release,root,'fresh')
            # Independently signed same-ID content must not silently replace a known version.
            key=self.key;public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
            archive=Path(tmp)/'different.huerel'
            build_release({'agent/hue_agent/__init__.py':b'different'},archive,key,'0.2.0','mips-3.4','cp313',{'bumble':'0.0.236'})
            changed=verify_release(archive,public,'mips-3.4','cp313',{'bumble':'0.0.236'})
            with self.assertRaises(ValueError):install_candidate(changed,root,'repair')
            self.assertEqual((root/'releases/0.2.0/agent/hue_agent/__init__.py').read_bytes(),b'fixture')

    def test_active_repair_stops_owner_and_recovers_promotable_stage_without_archive(self):
        class PowerCut(BaseException):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'system';release=self.release(tmp);install_candidate(release,root,'fresh')
            pointer=root/'active.json';pointer.write_text('{"schema":1,"releaseId":"0.2.0"}');pointer.chmod(0o600)
            code=root/'releases/0.2.0/agent/hue_agent/__init__.py';code.write_bytes(b'bad')
            calls=[]
            def stop():calls.append('stop');return True
            def cut(phase):
                if phase=='replaced':raise PowerCut()
            with self.assertRaises(PowerCut):install_candidate(release,root,'repair',failpoint=cut,stop_active=stop)
            self.assertEqual(calls,['stop']);release.archive.unlink()
            result=recover_installation(root,release.trust_key,('mips-3.4','cp313',{'bumble':'0.0.236'}),stop)
            self.assertTrue(result['recovered']);self.assertEqual(code.read_bytes(),b'fixture')
            self.assertEqual(json.loads(pointer.read_text())['releaseId'],'0.2.0')
