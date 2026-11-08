import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch

from hue_admin.snapshot import capture, validate_layout, layout_for, capture_current


class SnapshotTests(unittest.TestCase):
    def test_unified_root_snapshot_uses_canonical_paths_and_preserves_identity(self):
        from hue_admin.service_manager import agent_paths
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name in ('config','secrets','state','logs','run'):(root/name).mkdir(mode=0o700)
            (root/'state/schema.json').write_text('{"schema":1}')
            layout,sources=layout_for(root,True)
            for name,path in sources.items():path.write_bytes(b'x'*64);path.chmod(0o600)
            sources['config/release-trust.pub'].write_bytes(b'p'*32)
            sources['config/device.json'].write_text('{"name":"fixture"}')
            sources['config/management.json'].write_text('{"publicOrigin":"https://fixture.test"}')
            sources['secrets/bond.json'].write_text('{"fixture":{}}')
            cfg=agent_paths(root)
            cfg.update(transport='usb:fixture',address='fixture-lamp',controller_public_address='fixture-adapter',port=8787,
                       cloud={'enabled':False},tunnel={'enabled':True,'port':8788,'token_file':str(root/'secrets/device.token')})
            sources['config/agent.json'].write_text(json.dumps(cfg))
            db=Path(layout['journal'])
            with closing(sqlite3.connect(db)) as connection:
                connection.execute('create table fixture (id int)');connection.commit()
            db.chmod(0o600)
            files,identity=capture_current(root)
            self.assertEqual(identity,{'adapter':'fixture-adapter','lamp':'fixture-lamp'})
            self.assertEqual(len(files),10)
            self.assertEqual(files['config/release-trust.pub'],b'p'*32)
            self.assertFalse(any('hue-agent/' in str(p) for p in sources.values()))
            from hue_admin.backup import seal
            from hue_admin.restore_current import restore_from
            archive=root/'private.huebak';archive.write_bytes(seal(files,'fixture-password',identity));archive.chmod(0o600)
            with patch('hue_admin.restore_current.SYSTEM',root),patch('hue_admin.restore_current.management_lock',return_value=nullcontext()):
                result=restore_from(archive,'fixture-password',True,lambda *_:self.fail('dry-run changed service'),lambda:None)
            self.assertEqual(result,{'verified':True,'files':10,'installationChanged':False})

    def test_config_cannot_redirect_to_other_private_files(self):
        with self.assertRaises(ValueError):
            validate_layout({'keystore':'/etc/shadow'})

    def test_snapshot_includes_committed_wal_without_copying_wal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/'journal.sqlite'
            connection=sqlite3.connect(db)
            try:
                connection.execute('pragma journal_mode=WAL')
                connection.execute('create table evidence (value text)')
                connection.execute("insert into evidence values ('committed')")
                connection.commit()
                config=root/'config.json'
                config.write_text('{"safe":true}',encoding='utf-8')
                config.chmod(0o600)
                db.chmod(0o600)
                files=capture({'config/agent.json':config},db)
                restored=root/'snapshot.sqlite'
                restored.write_bytes(files['state/commands.sqlite'])
                with closing(sqlite3.connect(restored)) as recovered:
                    self.assertEqual(recovered.execute('select value from evidence').fetchall(),[('committed',)])
                self.assertEqual(set(files),{'config/agent.json','state/commands.sqlite'})
            finally:
                connection.close()

    def test_source_symlink_is_refused(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);real=root/'private';real.write_bytes(b'private');real.chmod(0o600)
            link=root/'link'
            try:os.symlink(real,link)
            except OSError:self.skipTest('Native symlink test required')
            with self.assertRaises((ValueError,OSError)):
                capture({'secrets/bond.json':link},None)
