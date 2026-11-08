import tempfile
import os
import csv
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deploy.build_release import guard_private_path


class SigningPathTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows ACL integration')
    def test_system_acl_cmdlet_does_not_depend_on_inherited_module_path(self):
        system=Path(os.environ['SystemRoot'])/'System32'
        result=subprocess.run([str(system/'whoami.exe'),'/user','/fo','csv','/nh'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0)
        sid=next(csv.reader(result.stdout.splitlines()))[-1]
        with tempfile.TemporaryDirectory() as tmp:
            # Python's Windows mode-0700 temp folder can add an explicit application SID.
            # Reset the empty fixture before constructing the intended user-only DACL.
            result=subprocess.run([str(system/'icacls.exe'),tmp,'/reset','/Q'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.assertEqual(result.returncode,0)
            result=subprocess.run([str(system/'icacls.exe'),tmp,'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F','/Q'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.assertEqual(result.returncode,0)
            from deploy.build_release import ROOT
            env=dict(os.environ);env['PSModulePath']=''
            result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'deploy/check-private-path.ps1'),'-Path',tmp],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.assertEqual(result.returncode,0)

    def test_windows_guard_failure_prevents_use(self):
        with tempfile.TemporaryDirectory() as tmp,patch('deploy.build_release.subprocess.run',return_value=SimpleNamespace(returncode=2)) as run:
            with self.assertRaises(ValueError):guard_private_path(Path(tmp),windows=True)
            self.assertIs(run.call_args.kwargs['shell'],False)

    def test_windows_guard_accepts_only_success(self):
        with tempfile.TemporaryDirectory() as tmp,patch('deploy.build_release.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            guard_private_path(Path(tmp),windows=True)
