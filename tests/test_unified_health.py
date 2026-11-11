import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from hue_admin.unified_health import status


class UnifiedHealthTests(unittest.TestCase):
    def check(self,old=False,dedicated=False):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);(root/'secrets').mkdir();token=root/'secrets/api.token';token.write_text('x'*64);token.chmod(0o600)
            module='hue_admin.unified_health.manager.'
            stack.enter_context(patch(module+'selection',return_value='0.2.0'))
            stack.enter_context(patch(module+'owner_record',return_value={'releaseId':'0.1.0' if old else '0.2.0'}))
            stack.enter_context(patch(module+'supervisor_owned',return_value=True))
            stack.enter_context(patch(module+'child_pid',return_value=123))
            stack.enter_context(patch(module+'child_owned',return_value=True))
            stack.enter_context(patch('hue_admin.unified_health.probe',side_effect=lambda port,*_:(200,{'status':'verified'} if port==8787 else {})))
            stack.enter_context(patch('hue_admin.runtime_policy.release_policy',return_value={'agent':'dedicated' if dedicated else 'root','tunnel':'dedicated'}))
            stack.enter_context(patch('hue_admin.privileges.load_service_identity',side_effect=lambda root,name:
                {'uid':61001,'gid':61001} if name=='hue-agent' else {'uid':61000,'gid':61000}))
            call=stack.enter_context(patch('hue_admin.unified_health.authenticated_probe',return_value=(200,{'status':'verified'})))
            value=status(True,root)
            self.probe_call=call.call_args
            self.checked_root=root
            return value

    def test_current_owned_version_and_live_state_are_both_required(self):
        value=self.check()
        self.assertTrue(value['healthy']);self.assertEqual(value['releaseId'],'0.2.0')
        self.assertEqual(value['components']['reporting']['state'],'unknown')
        self.assertNotIn('x'*64,str(value))

    def test_old_owner_http_readiness_cannot_claim_new_release(self):
        value=self.check(old=True)
        self.assertFalse(value['healthy']);self.assertIsNone(value['releaseId'])

    def test_signed_dedicated_contract_checks_actual_agent_uid_and_socket(self):
        from hue_admin.agent_storage import agent_runtime_paths
        self.assertTrue(self.check(dedicated=True)['healthy'])
        args,kwargs=self.probe_call
        self.assertEqual(args[4],agent_runtime_paths(self.checked_root,'0.2.0')['health_socket'])
        self.assertEqual(kwargs,{'expected_uid':61001,'expected_gid':61001})

    def test_legacy_contract_keeps_root_socket_even_if_service_account_exists(self):
        self.assertTrue(self.check()['healthy'])
        args,kwargs=self.probe_call
        self.assertEqual(args[4],self.checked_root/'run/health.sock')
        self.assertEqual(kwargs,{})

    def test_health_exposes_expected_role_ids_for_operator_diagnosis(self):
        value=self.check(dedicated=True)
        self.assertEqual(value['principals'],{
            'agent':{'expectedUid':61001,'expectedGid':61001},
            'tunnel':{'expectedUid':61000,'expectedGid':61000}})
        self.assertEqual(self.check()['principals']['agent'],{'expectedUid':0,'expectedGid':0})

    def test_live_probe_errors_or_invalid_payloads_degrade_safely_with_ble_parity(self):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);(root/'secrets').mkdir();token=root/'secrets/api.token';token.write_text('x'*64);token.chmod(0o600)
            module='hue_admin.unified_health.manager.'
            stack.enter_context(patch(module+'selection',return_value='0.2.0'))
            owner=stack.enter_context(patch(module+'owner_record',return_value={'releaseId':'0.2.0'}))
            stack.enter_context(patch(module+'supervisor_owned',return_value=True))
            stack.enter_context(patch(module+'child_pid',return_value=123))
            stack.enter_context(patch(module+'child_owned',return_value=True))
            stack.enter_context(patch('hue_admin.unified_health.probe',return_value=(200,{})))
            stack.enter_context(patch('hue_admin.runtime_policy.release_policy',return_value={'agent':'root','tunnel':'dedicated'}))
            probe=stack.enter_context(patch('hue_admin.unified_health.authenticated_probe',return_value=(200,['not-a-dict'])))
            value=status(True,root)
            self.assertFalse(value['healthy'])
            self.assertEqual(value['components']['localApi']['reason'],'LOCAL_CHECK_FAILED')
            self.assertEqual(value['components']['ble']['reason'],'LOCAL_CHECK_FAILED')
            probe.side_effect=OSError('down')
            value=status(True,root)
            self.assertEqual(value['components']['ble']['reason'],'LOCAL_CHECK_FAILED')
            probe.side_effect=None;probe.return_value=(200,{'status':'verified'})
            owner.side_effect=[{'releaseId':'0.2.0'},{'releaseId':'0.2.0'},None]
            value=status(True,root)
            self.assertFalse(value['healthy'])
            self.assertEqual(value['components']['localApi']['reason'],'LOCAL_CHECK_FAILED')
            self.assertEqual(value['components']['ble']['reason'],'LOCAL_CHECK_FAILED')

    def test_bootstrap_sync_status_fails_soft_and_reports_drift(self):
        import json
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        from hue_admin.releases import build_release,verify_release
        from hue_admin.installation import install_candidate
        minimal=self.check()
        self.assertIsNone(minimal['bootstrapReleaseId'])
        self.assertFalse(minimal['bootstrapInSync'])
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp)/'system';key=Ed25519PrivateKey.generate();pub=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
            archive=Path(tmp)/'r.huerel';runtime=('mips-3.4','cp313',{'bumble':'0.0.236'})
            build_release({'manager/hue_admin/__init__.py':b'# a','manager/hue_admin/cli.py':b'# b'},archive,key,'0.2.0',*runtime)
            install_candidate(verify_release(archive,pub,*runtime),root,'fresh')
            (root/'state/bootstrap.json').write_text(json.dumps({
                'schema':1,'phase':'committed','releaseId':'0.1.0','manifestHash':'a'*64,
                'stage':'.bootstrap-stage-'+'b'*32,'backup':'backups/bootstrap-'+'c'*32,
                'cliPath':'/opt/bin/huectl','initPath':'/opt/etc/init.d/S97keenetic-hue'}))
            module='hue_admin.unified_health.manager.'
            stack.enter_context(patch(module+'selection',return_value='0.2.0'))
            stack.enter_context(patch(module+'owner_record',return_value={'releaseId':'0.2.0'}))
            stack.enter_context(patch(module+'supervisor_owned',return_value=True))
            stack.enter_context(patch(module+'child_pid',return_value=123))
            stack.enter_context(patch(module+'child_owned',return_value=True))
            drifted=status(False,root)
            self.assertEqual(drifted['bootstrapReleaseId'],'0.1.0')
            self.assertFalse(drifted['bootstrapInSync'])
            (root/'manager/hue_admin').mkdir(parents=True);(root/'manager/hue_admin/__init__.py').write_bytes(b'# a')
            (root/'manager/hue_admin/cli.py').write_bytes(b'# b')
            (root/'state/bootstrap.json').write_text(json.dumps({
                'schema':1,'phase':'committed','releaseId':'0.2.0','manifestHash':'a'*64,
                'stage':'.bootstrap-stage-'+'b'*32,'backup':'backups/bootstrap-'+'c'*32,
                'cliPath':'/opt/bin/huectl','initPath':'/opt/etc/init.d/S97keenetic-hue'}))
            synced=status(False,root)
            self.assertEqual(synced['bootstrapReleaseId'],'0.2.0')
            self.assertTrue(synced['bootstrapInSync'])
