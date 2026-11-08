import ast
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from typing import Callable, cast
from types import SimpleNamespace
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'deploy/storage_ram_prepare.py'
spec = importlib.util.spec_from_file_location('storage_ram_prepare', path)
if spec is None or spec.loader is None:
    raise RuntimeError('storage_ram_prepare test module could not be loaded')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class PayloadTests(unittest.TestCase):
    def test_generated_remote_program_compiles_without_replacing_error_labels(self):
        script = module.remote_script({'schema': 1}, 'prepare')
        compile(script, '<remote>', 'exec')
        self.assertIn("RuntimeError('PAYLOAD_HASH')", script)

    def test_operation_mode_and_id_are_literal_allowlisted_arguments(self):
        identifier='00000000-0000-4000-8000-000000000001'
        self.assertEqual(module.dispatch_args('prepare'), [])
        self.assertEqual(module.dispatch_args('start'), ['start'])
        self.assertEqual(module.dispatch_args('status',identifier), ['status',identifier])
        script=module.remote_script({'schema':1},'status',identifier)
        compile(script,'<remote>','exec')
        self.assertIn("ACTION=['status', '00000000-0000-4000-8000-000000000001']",script)
        compile(module.ENTRY,'<entry>','exec')
        self.assertIn('dispatch(sys.argv[1:],runtime,factory)',module.ENTRY)
        self.assertIn("str(root/'prepare.py'),*ACTION",module.PROVISION)
        self.assertNotIn("write_text('ACTION='+repr(ACTION)",module.PROVISION)
        for action,operation in [('start','../../opt'),('status',None),('prepare',identifier)]:
            with self.assertRaises(ValueError):module.dispatch_args(action,operation)

    def tools(self, data=b'fixture'):
        return {'files': {k: base64.b64encode(data).decode() for k in module.TOOLS | {'mke2fs'}},
                'links': dict(module.LINKS)}

    def fixture_pins(self, data=b'fixture'):
        digest = hashlib.sha256(data).hexdigest()
        return {name: digest for name in module.TOOLS}

    def test_production_payload_strips_formatter_and_keeps_code_only(self):
        with patch.object(module, 'PINNED_TOOL_SHA256', self.fixture_pins(), create=True):
            result = module.build_payload({'hue_admin/__init__.py': b''}, self.tools())
        self.assertNotIn('mke2fs', result['tools']['files'])
        self.assertEqual(set(result['tools']['files']), module.TOOLS)
        self.assertEqual(set(result['sources']), {'hue_admin/__init__.py'})
        self.assertEqual(result['schema'], 1)

    def test_local_payload_rejects_self_hashed_tool_substitution(self):
        pins = self.fixture_pins(b'trusted tool')
        with patch.object(module, 'PINNED_TOOL_SHA256', pins, create=True):
            with self.assertRaisesRegex(ValueError, 'STORAGE_PREPARATION_TOOL_HASH'):
                module.build_payload({'hue_admin/__init__.py': b''}, self.tools(b'attacker tool'))

    def test_root_packer_rejects_self_hashed_unpinned_tool_before_writing(self):
        tree = ast.parse(module.PROVISION)
        packed = next(node for node in tree.body
                      if isinstance(node, ast.FunctionDef) and node.name == 'packed')
        namespace = {'base64': base64, 'hashlib': hashlib}
        exec(compile(ast.Module(body=[packed], type_ignores=[]), '<root-packed>', 'exec'), namespace)
        pack = cast(Callable[..., None], namespace['packed'])
        data = b'attacker tool'
        payload = {'data': base64.b64encode(data).decode(),
                   'sha256': hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'e2fsck'
            with self.assertRaisesRegex(RuntimeError, 'TOOL_HASH'):
                pack(payload, target, expected_hash=hashlib.sha256(b'trusted tool').hexdigest())
            self.assertFalse(target.exists())

            legitimate = {'data': base64.b64encode(b'trusted tool').decode(),
                          'sha256': hashlib.sha256(b'trusted tool').hexdigest()}
            pack(legitimate, target, expected_hash=legitimate['sha256'])
            self.assertEqual(target.read_bytes(), b'trusted tool')

    def test_unexpected_tool_or_link_is_rejected(self):
        tools = self.tools(); tools['files']['sh'] = 'Zg=='
        with self.assertRaises(ValueError): module.build_payload({}, tools)
        tools = self.tools(); tools['links']['libss.so.2'] = '../../opt/lib/libss.so.2'
        with self.assertRaises(ValueError): module.build_payload({}, tools)

    def test_sources_cannot_include_tests_secrets_paths_or_symlink_descriptions(self):
        for key, value in [('tests/a.py', b'x'), ('hue_admin/../a.py', b'x'),
                           ('hue_admin/config.json', b'x'), ('hue_admin/a.py', {'link': '/opt'})]:
            with self.assertRaises(ValueError): module.build_payload({key: value}, self.tools())


class LocalSettingsTests(unittest.TestCase):
    def fixture(self, directory):
        root=Path(directory);key=root/'operator-key';key.write_text('fixture-only')
        settings=root/'settings.json'
        value={'ssh_host':'router.example','ssh_port':2022,'ssh_user':'operator','ssh_key_path':key.name}
        settings.write_text(json.dumps(value))
        return settings,value,key

    def test_explicit_settings_build_strict_ssh_arguments_and_resolve_relative_key(self):
        with tempfile.TemporaryDirectory() as directory:
            settings,_,key=self.fixture(directory)
            command=module.ssh_command(module.load_settings(settings))
            self.assertEqual(command[:7],['ssh','-T','-p','2022','-i',str(key),'-o'])
            self.assertIn('StrictHostKeyChecking=yes',command)
            self.assertIn('BatchMode=yes',command)
            self.assertIn('IdentitiesOnly=yes',command)
            self.assertEqual(command[-2:],['operator@router.example','python3 -'])

    def test_blank_example_missing_file_and_invalid_fields_fail_before_ssh(self):
        example=path.parent/'storage-ram.example.json'
        with patch('subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'STORAGE_SETTINGS_'):
                module.load_settings(example)
            with tempfile.TemporaryDirectory() as directory:
                settings,value,_=self.fixture(directory)
                for field,replacement in [('ssh_host',''),('ssh_host','-oProxyCommand=bad'),
                                          ('ssh_user','root;bad'),('ssh_port',True),
                                          ('ssh_port',0),('ssh_port',65536),('ssh_key_path','absent')]:
                    settings.write_text(json.dumps({**value,field:replacement}))
                    with self.subTest(field=field,value=replacement),self.assertRaisesRegex(ValueError,'STORAGE_SETTINGS_'):
                        module.load_settings(settings)
                with self.assertRaisesRegex(ValueError,'STORAGE_SETTINGS_'):
                    module.load_settings(Path(directory)/'absent.json')
            run.assert_not_called()

    def test_main_rejects_unfilled_settings_before_reading_payload_or_connecting(self):
        with patch('sys.argv',['storage_ram_prepare.py','absent-payload.json','--settings',str(path.parent/'storage-ram.example.json')]),patch('subprocess.run') as run:
            with self.assertRaises(SystemExit) as error:module.main()
            self.assertEqual(error.exception.code,2)
            run.assert_not_called()

    def test_main_uses_local_settings_without_a_real_connection_or_implicit_log(self):
        with tempfile.TemporaryDirectory() as directory:
            settings,_,key=self.fixture(directory)
            payload=Path(directory)/'payload.json';payload.write_text('{}')
            argv=['storage_ram_prepare.py',str(payload),'--settings',str(settings)]
            result=SimpleNamespace(stdout=b'fixture output',returncode=0)
            with patch('sys.argv',argv),patch('sys.stdout',new_callable=io.StringIO), \
                 patch.object(module,'build_payload',return_value={'schema':1}), \
                 patch.object(module,'remote_script',return_value='fixture program'), \
                 patch('subprocess.run',return_value=result) as run:
                self.assertEqual(module.main(),0)
            command=run.call_args.args[0]
            self.assertEqual(command[-2],'operator@router.example')
            self.assertEqual(command[3],'2022');self.assertEqual(command[5],str(key))
            self.assertEqual(run.call_args.kwargs['input'],b'fixture program')
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),
                             ['operator-key','payload.json','settings.json'])


if __name__ == '__main__': unittest.main()
