import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hue_admin.migration import LegacyLayout, migrate, recover_if_needed


class PowerCut(BaseException): pass


class ExistingMigrationTests(unittest.TestCase):
    def fixture(self, tmp):
        base=Path(tmp); root=base/'system'
        for name in ('config','secrets','state','backups','run','logs','releases'):
            (root/name).mkdir(mode=0o700,parents=True)
        layout=LegacyLayout(base/'agent',base/'tunnel',base/'ble',base/'S99',base/'S98')
        for folder in layout.directories().values():folder.mkdir(mode=0o700)
        cfg={'device_config':str(layout.ble/'device.json'),'keystore':str(layout.ble/'keystore.json'),
             'journal':str(layout.agent/'commands.sqlite'),'token_file':str(layout.agent/'api.token'),
             'lock_file':str(layout.agent/'agent.lock'),'log_file':str(layout.agent/'agent.log'),
             'pid_file':str(layout.agent/'agent.pid'),'transport':'usb:fixture','address':'lamp',
             'controller_public_address':'adapter','port':8787,'cloud':{'enabled':False},
             'tunnel':{'enabled':True,'port':8788,'token_file':str(layout.agent/'cloud.token')}}
        data={layout.agent/'config.json':json.dumps(cfg).encode(),layout.ble/'device.json':b'{"name":"fixture"}',
              layout.ble/'keystore.json':b'{"lamp":{"ltk":"fixture"}}',layout.agent/'api.token':b'a'*64,
              layout.agent/'cloud.token':b'b'*64,layout.tunnel/'tunnel.token':b'c'*64,
              root/'config/management.json':b'{"publicOrigin":"https://fixture.invalid"}',
              root/'secrets/setup-issuer.token':b'd'*64,root/'config/release-trust.pub':b'k'*32}
        for path,value in data.items():path.write_bytes(value);path.chmod(0o600)
        for path in layout.entries().values():path.write_bytes(b'#!/bin/sh\n# fixture\n');path.chmod(0o700)
        db=sqlite3.connect(layout.agent/'commands.sqlite');db.execute('create table commands(id integer)');db.commit();db.close()
        (layout.agent/'commands.sqlite').chmod(0o600)
        slot=root/'releases/0.2.0';slot.mkdir();(slot/'metadata').mkdir()
        (slot/'metadata/state-schema.json').write_bytes(b'{"schema":1,"read":[1],"write":1}')
        (slot/'metadata/state-schema.json').chmod(0o600)
        calls=[]
        def legacy(action):calls.append(('legacy',action));return True
        def new(action):calls.append(('new',action));return True
        return root,layout,slot,data,calls,legacy,new

    def test_success_copies_identity_archives_old_layout_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            with patch('hue_admin.migration.verify_slot',return_value=slot):
                result=migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:{'healthy':True,'releaseId':'0.2.0'},layout=layout)
                again=migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:None,layout=layout)
            self.assertTrue(result['migrated']);self.assertTrue(again['alreadyMigrated'])
            self.assertEqual((root/'secrets/bond.json').read_bytes(),data[layout.ble/'keystore.json'])
            self.assertEqual(json.loads((root/'config/agent.json').read_bytes())['address'],'lamp')
            self.assertTrue(all(not p.exists() for p in layout.entries().values()))
            self.assertTrue(all(not p.exists() for p in layout.directories().values()))
            self.assertTrue(all((root/result['backup']/'legacy'/name).is_dir() for name in layout.directories()))
            self.assertEqual(calls,[('legacy','stop'),('new','start')])

    def test_every_interruption_rolls_back_before_legacy_start(self):
        points=('prepared','disabled:agent','disabled:tunnel','stopped','copied:config/agent.json','copied',
                'selected','started','verified','archived:agent','archived:tunnel','archived:ble')
        for point in points:
            with self.subTest(point=point),tempfile.TemporaryDirectory() as tmp:
                root,layout,slot,data,calls,old,new=self.fixture(tmp)
                def cut(phase):
                    if phase==point:raise PowerCut()
                with patch('hue_admin.migration.verify_slot',return_value=slot),self.assertRaises(PowerCut):
                    migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:{'healthy':True,'releaseId':'0.2.0'},layout=layout,failpoint=cut)
                recover_if_needed(root,old,new,layout=layout)
                self.assertTrue(all(p.exists() for p in layout.entries().values()))
                self.assertTrue(all(p.exists() for p in layout.directories().values()))
                self.assertFalse((root/'active.json').exists());self.assertFalse((root/'state/schema.json').exists())
                self.assertEqual((layout.ble/'keystore.json').read_bytes(),data[layout.ble/'keystore.json'])
                self.assertEqual(calls[-1],('legacy','start'))
                self.assertEqual(recover_if_needed(root,old,new,layout=layout),{'pending':False})

    def test_wrong_version_health_rolls_back_and_never_swallows_stop_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            with patch('hue_admin.migration.verify_slot',return_value=slot),self.assertRaises(RuntimeError):
                migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:{'healthy':True,'releaseId':'older'},layout=layout)
            self.assertFalse((root/'active.json').exists());self.assertTrue(layout.agent.exists())
            self.assertEqual(calls[-2:],[('new','stop'),('legacy','start')])

    def test_symlink_and_unexpected_legacy_path_rejected_before_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            cfg=json.loads((layout.agent/'config.json').read_bytes());cfg['keystore']=str(Path(tmp)/'unrelated')
            (layout.agent/'config.json').write_text(json.dumps(cfg))
            with patch('hue_admin.migration.verify_slot',return_value=slot),self.assertRaises(ValueError):
                migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:None,layout=layout)
            self.assertEqual(calls,[])

    def test_actual_legacy_adapter_lock_in_ble_directory_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            cfg=json.loads((layout.agent/'config.json').read_bytes());cfg['lock_file']=str(layout.ble/'agent.lock')
            (layout.agent/'config.json').write_text(json.dumps(cfg))
            with patch('hue_admin.migration.verify_slot',return_value=slot):
                result=migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:{'healthy':True,'releaseId':'0.2.0'},layout=layout)
            self.assertTrue(result['migrated'])

    def test_boot_recovery_refuses_changed_backup_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            def cut(phase):
                if phase=='selected':raise PowerCut()
            with patch('hue_admin.migration.verify_slot',return_value=slot),self.assertRaises(PowerCut):
                migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:None,layout=layout,failpoint=cut)
            receipt=json.loads((root/'state/migration.json').read_bytes())
            (root/receipt['backup']/'entries/agent').write_bytes(b'changed')
            with self.assertRaises(ValueError):recover_if_needed(root,old,new,layout=layout)
            self.assertNotEqual(calls[-1],('legacy','start'))

    def test_rollback_preserves_new_command_reservations_and_failed_stop_blocks_legacy_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            def updated():
                db=sqlite3.connect(root/'state/commands.sqlite');db.execute('insert into commands values(42)');db.commit();db.close()
                return {'healthy':False}
            with patch('hue_admin.migration.verify_slot',return_value=slot),self.assertRaises(RuntimeError):
                migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,updated,layout=layout)
            db=sqlite3.connect(layout.agent/'commands.sqlite')
            self.assertEqual(db.execute('select id from commands').fetchall(),[(42,)]);db.close()
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            def cut(phase):
                if phase=='selected':raise PowerCut()
            with patch('hue_admin.migration.verify_slot',return_value=slot),self.assertRaises(PowerCut):
                migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:None,layout=layout,failpoint=cut)
            calls.clear()
            with self.assertRaises(RuntimeError):recover_if_needed(root,old,lambda verb:False,layout=layout)
            self.assertEqual(calls,[]);self.assertTrue((root/'active.json').exists())

    def test_rollback_removes_legacy_journal_and_new_state_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            def updated():
                legacy_journal=layout.agent/'commands.sqlite-journal'
                legacy_journal.write_bytes(b'stale-legacy');legacy_journal.chmod(0o600)
                return {'healthy':False}
            with patch('hue_admin.migration.verify_slot',return_value=slot),self.assertRaises(RuntimeError):
                migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,updated,layout=layout)
            for suffix in ('-wal','-shm','-journal'):
                self.assertFalse((layout.agent/('commands.sqlite'+suffix)).exists())
        with tempfile.TemporaryDirectory() as tmp:
            root,layout,slot,data,calls,old,new=self.fixture(tmp)
            def cut(phase):
                if phase=='copied':
                    for suffix in ('-wal','-shm','-journal'):
                        side=root/('state/commands.sqlite'+suffix)
                        side.write_bytes(b'stale-new');side.chmod(0o600)
                    raise PowerCut()
            with patch('hue_admin.migration.verify_slot',return_value=slot),self.assertRaises(PowerCut):
                migrate(root,'0.2.0',b'k'*32,('fixture',),old,new,lambda:None,layout=layout,failpoint=cut)
            recover_if_needed(root,old,new,layout=layout)
            for suffix in ('-wal','-shm','-journal'):
                self.assertFalse((root/('state/commands.sqlite'+suffix)).exists())
