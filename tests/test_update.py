import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('hue_update', ROOT/'deploy/update.py')
update=importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)/'agent'
        (self.base/'app').mkdir(parents=True)
        (self.base/'app/old.py').write_text('old')
        (self.base/'config.json').write_text('{"port":8787}')
        (self.base/'api.token').write_text('unchanged')
        self.snapshot={'hue_agent/__init__.py':b'', 'hue_agent/cloud_link.py':b'# candidate',
                       'deploy/launch.py':b'# launcher'}

    def tearDown(self):self.temp.cleanup()

    def test_update_uses_snapshot_bytes_and_preserves_token(self):
        update.apply_snapshot(self.snapshot,self.base,{'port':8787,'cloud':{'enabled':False}},'g4-test')
        self.assertEqual((self.base/'app/hue_agent/cloud_link.py').read_bytes(),b'# candidate')
        self.assertEqual((self.base/'api.token').read_text(),'unchanged')
        self.assertEqual((self.base/'backups/g4-test/app/old.py').read_text(),'old')

    def test_failed_swap_restores_app_and_config(self):
        original=Path.rename
        def rename(path,target):
            if path.name=='app.new-g4-test':raise OSError('simulated failure')
            return original(path,target)
        with patch.object(Path,'rename',rename), self.assertRaises(OSError):
            update.apply_snapshot(self.snapshot,self.base,{'port':1},'g4-test')
        self.assertEqual((self.base/'app/old.py').read_text(),'old')
        self.assertEqual((self.base/'config.json').read_text(),'{"port":8787}')

    def test_refuses_existing_backup_symlink_or_unsafe_tag(self):
        for tag in ('../escape','x/y',''):
            with self.assertRaises(ValueError):update.apply_snapshot(self.snapshot,self.base,{},tag)
        (self.base/'backups/taken').mkdir(parents=True)
        with self.assertRaises(FileExistsError):update.apply_snapshot(self.snapshot,self.base,{},'taken')

    def test_untrusted_installer_digest_refused_before_code_runs(self):
        source=Path(self.temp.name)/'untrusted.py'
        source.write_text('raise RuntimeError("executed")')
        with self.assertRaisesRegex(ValueError,'digest'):
            update.load_trusted_snapshot(source,'0'*64,Path(self.temp.name),'0'*64)
