import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hue_admin.storage_acceptance import identity_from_fd, validate_evidence, require_current


IDENTITY={'filesystemUuid':'11111111-2222-3333-4444-555555555555',
          'usbVendor':'1234','usbProduct':'5678','usbSerial':'FIXTURE-ONLY',
          'capacityBytes':64*1024*1024}
BOOT='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'


def evidence():
    return {'schema':1,'kind':'offline-e2fsck','targetKind':'block','readOnly':True,
            'exitCode':0,'toolVersion':'1.47.3','runtimeSha256':'a'*64,
            'outputSha256':'b'*64,'bootId':BOOT,'checkedMonotonic':100,
            'mountsBefore':[],'mountsAfter':[],'identity':copy.deepcopy(IDENTITY)}


class EvidenceTests(unittest.TestCase):
    def test_matching_fresh_real_device_evidence(self):
        self.assertTrue(validate_evidence(evidence(),IDENTITY,BOOT,150))

    def test_failed_or_synthetic_or_mounted_result_is_not_acceptance(self):
        for change in ({'exitCode':4},{'exitCode':8},{'exitCode':False},
                       {'targetKind':'image'},{'kind':'fixture'},{'readOnly':False},
                       {'mountsBefore':['/opt']},{'mountsAfter':['/tmp/mnt/disk']},
                       {'runtimeSha256':'unknown'},{'toolVersion':'unknown'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_evidence({**evidence(),**change},IDENTITY,BOOT,150)

    def test_stale_other_boot_or_future_evidence_is_rejected(self):
        for boot,now in ((BOOT,1001),(BOOT,99),('ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee',150)):
            with self.subTest(boot=boot,now=now),self.assertRaises(ValueError):
                validate_evidence(evidence(),IDENTITY,boot,now)

    def test_each_device_identity_field_must_match(self):
        for key in IDENTITY:
            current={**IDENTITY,key:0 if key=='capacityBytes' else 'different'}
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_evidence(evidence(),current,BOOT,150)

    @unittest.skipUnless(os.name=='posix','POSIX descriptor type')
    def test_regular_image_descriptor_cannot_supply_real_usb_identity(self):
        with tempfile.TemporaryFile() as stream,patch('hue_admin.storage_acceptance.os.pread') as read:
            with self.assertRaisesRegex(ValueError,'STORAGE_BLOCK_REQUIRED'):
                identity_from_fd(stream.fileno())
            read.assert_not_called()


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Native private evidence')
class PrivateEvidenceTests(unittest.TestCase):
    def test_missing_private_evidence_blocks_before_device_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);root.chmod(0o700);(root/'state').mkdir(mode=0o700)
            with patch('hue_admin.storage_acceptance.current_storage') as probe:
                with self.assertRaises((ValueError,OSError)):require_current(root)
                probe.assert_not_called()


if __name__=='__main__':unittest.main()
