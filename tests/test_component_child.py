import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from hue_admin.agent_runtime import agent_environment
from hue_admin.agent_storage import agent_runtime_paths


class Executed(BaseException):pass


class ComponentChildTests(unittest.TestCase):
    script=Path(__file__).resolve().parents[1]/'deploy/component-child.py'

    def launch(self,root,*,dedicated=True,drop_error=None):
        release='0.2.0-rc.6';paths=agent_runtime_paths(root,release);events=[]
        policy={'agent':'dedicated' if dedicated else 'root','tunnel':'dedicated'}
        def execute(binary,args,env):
            events.append(('exec',binary,args,env));raise Executed()
        def drop(uid,gid):
            events.append(('drop',uid,gid))
            if drop_error:raise drop_error
        old_path=sys.path[:]
        try:
            with ExitStack() as stack:
                for name,value in (
                    ('sys.argv',[str(self.script),'agent',str(root),release,'123']),
                    ('os.close',lambda fd:None),('os.umask',lambda mode:None),('os.chdir',lambda path:None),
                    ('os.execve',execute),
                    ('hue_admin.runtime_policy.release_policy',lambda *a:policy),
                    ('hue_admin.service_manager.environment',lambda *a:{}),
                    ('hue_admin.service_manager.private_bytes',lambda *a:json.dumps({}).encode()),
                    ('hue_admin.service_manager.validate_agent_config',lambda *a:None),
                    ('hue_admin.account_store.ensure_accounts',lambda *a:events.append(('accounts',))),
                    ('hue_admin.usb_permissions.prepare_usb_permissions',lambda *a:events.append(('usb',))),
                    ('hue_admin.agent_runtime.prepare_agent_runtime',lambda *a:({'uid':61001,'gid':61001},paths)),
                    ('hue_admin.privileges.drop_identity',drop)):
                    stack.enter_context(patch(name,value))
                with self.assertRaises(type(drop_error) if drop_error else Executed):runpy.run_path(str(self.script),run_name='__main__')
        finally:sys.path[:]=old_path
        return events,paths

    def test_restricted_agent_prepares_then_drops_before_safe_python_exec(self):
        with tempfile.TemporaryDirectory() as directory:
            events,paths=self.launch(Path(directory))
            self.assertEqual(events[:3],[('accounts',),('usb',),('drop',61001,61001)])
            _,binary,args,env=events[-1]
            self.assertEqual(args,[sys.executable,'-s','-P','-u','-m','hue_agent','--config',str(paths['config'])])
            self.assertEqual(env,agent_environment(paths,'0.2.0-rc.6'))

    def test_failed_drop_never_executes_root_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            events,_=self.launch(Path(directory),drop_error=PermissionError('fixture'))
            self.assertFalse(any(event[0]=='exec' for event in events))

    def test_authenticated_legacy_agent_keeps_rollback_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);events,_=self.launch(root,dedicated=False)
            self.assertEqual(len(events),1)
            self.assertEqual(events[0][2],[sys.executable,'-u','-m','hue_agent','--config',str(root/'config/agent.json')])
