import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class ChildIdentityTests(unittest.TestCase):
    boot='12345678-1234-1234-1234-123456789abc'
    release='0.2.0-rc.6'

    def test_root_writer_pins_pid_start_ticks_boot_and_release(self):
        from hue_admin.child_identity import write_child_identity,child_identity_matches
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'run').mkdir()
            with patch('hue_admin.service_manager.process',return_value=('456',[])), \
                 patch('hue_admin.service_manager.boot_id',return_value=self.boot), \
                 patch('hue_admin.child_identity.os.getpid',return_value=123):
                write_child_identity(root,'agent',self.release)
            self.assertTrue(child_identity_matches(root,'agent',self.release,123,'456',self.boot))
            for pid,ticks,boot,release in ((124,'456',self.boot,self.release),
                    (123,'457',self.boot,self.release),(123,'456','00000000-0000-0000-0000-000000000000',self.release),
                    (123,'456',self.boot,'0.2.0-rc.5')):
                self.assertFalse(child_identity_matches(root,'agent',release,pid,ticks,boot))

    def test_missing_malformed_extra_or_mistyped_record_is_rejected(self):
        from hue_admin.child_identity import child_identity_matches
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'run').mkdir();path=root/'run/agent-child.json'
            self.assertFalse(child_identity_matches(root,'agent',self.release,123,'456',self.boot))
            valid={'schema':1,'pid':123,'releaseId':self.release,'bootId':self.boot,'startTicks':'456'}
            for value in ({**valid,'schema':True},{**valid,'pid':True},{**valid,'extra':0},[],'bad'):
                path.write_text(json.dumps(value));path.chmod(0o600)
                self.assertFalse(child_identity_matches(root,'agent',self.release,123,'456',self.boot))
