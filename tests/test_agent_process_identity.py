from pathlib import Path
import io
import unittest
from unittest.mock import patch

from hue_admin.agent_storage import agent_runtime_paths


class AgentProcessIdentityTests(unittest.TestCase):
    def fixture(self):
        from hue_admin.agent_runtime import agent_environment
        root=Path('/fixture/system');release='0.2.0-rc.6';paths=agent_runtime_paths(root,release)
        args=['/opt/bin/python3','-s','-P','-u','-m','hue_agent','--config',str(paths['config'])]
        status='Uid: 61001 61001 61001 61001\nGid: 61001 61001 61001 61001\nGroups:\nCapEff: 0\nCapPrm: 0\nCapBnd: 0\nCapInh: 0\nCapAmb: 0\n'
        env={k.encode():v.encode() for k,v in agent_environment(paths,release).items()}
        return root,release,args,status,env

    def test_exact_runtime_contract_is_accepted(self):
        from hue_admin.agent_runtime import agent_process_matches
        self.assertTrue(agent_process_matches(*self.fixture(),uid=61001,gid=61001,python='/opt/bin/python3'))

    def test_root_saved_uid_caps_or_supplementary_group_is_rejected(self):
        from hue_admin.agent_runtime import agent_process_matches
        root,release,args,status,env=self.fixture()
        for changed in (status.replace('61001 61001 61001 61001','61001 61001 0 61001',1),
                        status.replace('CapEff: 0','CapEff: 1'),status.replace('Groups:','Groups: 0'),
                        status.replace('Groups:\n','')):
            with self.subTest(status=changed):
                self.assertFalse(agent_process_matches(root,release,args,changed,env,uid=61001,gid=61001,python='/opt/bin/python3'))

    def test_unsafe_python_flags_or_wrong_config_is_rejected(self):
        from hue_admin.agent_runtime import agent_process_matches
        root,release,args,status,env=self.fixture()
        for changed in (args[:1]+args[2:],args[:-1]+['/master/config.json']):
            self.assertFalse(agent_process_matches(root,release,changed,status,env,uid=61001,gid=61001,python='/opt/bin/python3'))

    def test_wrong_release_pythonpath_or_environment_is_rejected(self):
        from hue_admin.agent_runtime import agent_process_matches
        root,release,args,status,env=self.fixture()
        for change in ({b'PYTHONPATH':b'/writable'}, {b'HUE_RELEASE_ID':b'0.2.0-rc.5'},
                       {b'PYTHONSTARTUP':b'/writable/start.py'}):
            self.assertFalse(agent_process_matches(root,release,args,status,{**env,**change},uid=61001,gid=61001,python='/opt/bin/python3'))

    def test_manager_uses_signed_agent_contract(self):
        from hue_admin import service_manager as manager
        root,release,args,status,env=self.fixture();pid=123
        record={'bootId':'fixture-boot','releaseId':release,'ownerFd':9}
        raw=b'\0'.join(key+b'='+value for key,value in env.items())+b'\0'
        with patch.object(manager,'boot_id',return_value='fixture-boot'), \
             patch.object(manager,'process',return_value=('100',[value.encode() for value in args])), \
             patch('hue_admin.runtime_policy.release_policy',return_value={'agent':'dedicated','childIdentity':True}), \
             patch('hue_admin.child_identity.child_identity_matches',return_value=True) as generation, \
             patch('hue_admin.privileges.load_service_identity',return_value={'uid':61001,'gid':61001}), \
             patch.object(Path,'open',return_value=io.BytesIO(raw)), \
             patch.object(Path,'read_text',return_value=status):
            self.assertTrue(manager.child_owned('agent',root,record,pid))
            generation.assert_called_once_with(root,'agent',release,pid,'100','fixture-boot')
            generation.return_value=False
            self.assertFalse(manager.child_owned('agent',root,record,pid))
