from contextlib import nullcontext
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_activation as fixtures
from hue_admin.release_commands import execute


class ReleaseCommandTests(unittest.TestCase):
    def test_update_does_not_cross_an_unfinished_runtime_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture=fixtures.ActivationTests();root,key,runtime=fixture.fixture(tmp);events=[]
            pending=root/'state/runtime-repair.json'
            pending.write_text(json.dumps({'schema':1,'phase':'prepared','releaseId':fixture.current(root),
                                           'backup':'backups/runtime-repair-'+'a'*32}));pending.chmod(0o600)
            with patch('hue_admin.release_commands.management_lock',return_value=nullcontext()), \
                 patch('hue_admin.release_commands.runtime_for',return_value=runtime):
                with self.assertRaisesRegex(RuntimeError,'RUNTIME_REPAIR_RECOVERY_REQUIRED'):
                    execute('update',str(Path(tmp)/'0.2.0.huerel'),root=root,
                            service=lambda *args:events.append(args) or True,
                            health=lambda:{'healthy':True,'releaseId':fixture.current(root)})
            self.assertEqual(events,[])

    def test_update_uses_stop_start_and_exact_version_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture=fixtures.ActivationTests();root,key,runtime=fixture.fixture(tmp);events=[]
            service=lambda component,verb:events.append((component,verb)) or True
            health=lambda:{'healthy':True,'releaseId':fixture.current(root)}
            with patch('hue_admin.release_commands.management_lock',return_value=nullcontext()),patch('hue_admin.release_commands.runtime_for',return_value=runtime):
                result=execute('update',str(Path(tmp)/'0.2.0.huerel'),root=root,service=service,health=health)
            self.assertTrue(result['activated']);self.assertEqual(fixture.current(root),'0.2.0')
            self.assertTrue(result['bootstrapSyncRequired'])
            self.assertEqual(events,[('tunnel','stop'),('agent','stop'),('agent','start'),('tunnel','start')])

    def test_unconfigured_boot_never_touches_legacy_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            with patch('hue_admin.release_commands.management_lock',return_value=nullcontext()):
                result=execute('internal-service',boot_action='start',root=Path(tmp),service=lambda *args:calls.append(args),health=lambda:{})
            self.assertFalse(result['configured']);self.assertEqual(calls,[])
