"""Recovery permissions and preservation, using disposable native fixtures."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from hue_admin import agent_snapshot
import test_sqlite_crash_recovery as crash
from test_sqlite_crash_recovery import fingerprint


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,
                     'Native root/dedicated-agent boundary')
class AgentRecoveryTests(unittest.TestCase):
    def fixture(self, directory):
        return crash.StoppedSnapshotCrashTests().fixture(directory)

    def recover(self,path,output):
        return agent_snapshot.snapshot_agent_database(path,61001,61001,output_dir=output,recover_hot=True)

    def test_pair_preserved_before_recovery_and_root_does_not_parse_sqlite(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory);side=Path(str(path)+'-journal')
            original=[p.read_bytes() for p in (path,side)]
            with patch('sqlite3.connect',side_effect=AssertionError('root parsed SQLite')):
                data=self.recover(path,output)
            self.assertTrue(data.startswith(b'SQLite format 3\x00'))
            archives=list(output.glob('sqlite-recovery-*'));self.assertEqual(len(archives),1)
            archive=archives[0];self.assertEqual(archive.stat().st_mode&0o777,0o700)
            for name,value in zip(('database.sqlite','database.sqlite-journal'),original):
                saved=archive/name;self.assertEqual(saved.read_bytes(),value)
                self.assertEqual(saved.stat().st_uid,0);self.assertEqual(saved.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads((archive/'receipt.json').read_text())['phase'],'preserved')
            self.assertEqual(list(path.parent.parent.glob('.sqlite-recovery-*')),[])
            self.assertTrue(side.exists())
            before=fingerprint((path,));self.recover(path,output)
            self.assertEqual(before,fingerprint((path,)));self.assertEqual(len(list(output.glob('sqlite-recovery-*'))),2)

    def test_unsafe_sidecars_reject_without_changing_data(self):
        for kind in ('symlink','hardlink','wrong_owner','wrong_mode','oversized','wal','shm'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                path,output=self.fixture(directory);side=Path(str(path)+'-journal')
                before=path.read_bytes()
                if kind in ('symlink','hardlink'):
                    saved=side.with_name('original-journal');side.rename(saved)
                    if kind=='symlink':side.symlink_to(saved)
                    else:os.link(saved,side)
                elif kind=='wrong_owner':os.chown(side,0,0)
                elif kind=='wrong_mode':side.chmod(0o644)
                elif kind=='oversized':
                    with side.open('r+b') as f:f.truncate(32*1024*1024+1)
                else:
                    extra=Path(str(path)+'-'+kind);extra.write_bytes(b'fixture')
                with self.assertRaisesRegex(ValueError,'AGENT_RECOVERY'):
                    self.recover(path,output)
                self.assertEqual(path.read_bytes(),before);self.assertTrue(side.exists())

    def test_insufficient_space_preserves_original_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory);pair=(path,Path(str(path)+'-journal'));before=fingerprint(pair)
            with patch('hue_admin.agent_storage.shutil.disk_usage') as usage:
                usage.return_value.free=0
                with self.assertRaisesRegex(ValueError,'AGENT_RECOVERY_SPACE'):self.recover(path,output)
            self.assertEqual(fingerprint(pair),before)

    def test_changed_input_aborts_before_sqlite_recovery(self):
        from hue_admin import agent_storage
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory);side=Path(str(path)+'-journal');original=side.read_bytes()
            real=agent_storage._copy_recovery_file
            def changed(*args,**kwargs):
                result=real(*args,**kwargs)
                with path.open('ab') as f:f.write(b'changed')
                return result
            with patch.object(agent_storage,'_copy_recovery_file',side_effect=changed):
                with self.assertRaisesRegex(ValueError,'AGENT_RECOVERY_CHANGED'):self.recover(path,output)
            self.assertEqual(side.read_bytes(),original)

    def test_failure_after_rollback_keeps_preservation_and_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory)
            worker=agent_snapshot.WORKER.replace('phase=5','raise SystemExit(78)\n   phase=5')
            with patch.object(agent_snapshot,'WORKER',worker):
                with self.assertRaises(RuntimeError):self.recover(path,output)
            self.assertEqual(len(list(output.glob('sqlite-recovery-*'))),1)
            self.assertTrue(self.recover(path,output).startswith(b'SQLite format 3\x00'))

    def test_journal_replaced_after_validation_cannot_mutate_original_main(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory);original=path.read_bytes()
            hook="side=Path(sys.argv[1]+'-journal');raw=side.read_bytes();side.unlink();side.write_bytes(raw);side.chmod(0o600)\n  "
            worker=agent_snapshot.WORKER.replace("mode='rw' if expected_recovery is not None else 'ro'",hook+"mode='rw' if expected_recovery is not None else 'ro'")
            with patch.object(agent_snapshot,'WORKER',worker):
                with self.assertRaisesRegex(ValueError,'AGENT_RECOVERY_CHANGED'):self.recover(path,output)
            self.assertEqual(path.read_bytes(),original)

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory);path.unlink()
            with self.assertRaisesRegex(ValueError,'AGENT_RECOVERY_FILE'):
                self.recover(path,output)
            self.assertFalse(path.exists())

    def test_corrupt_database_stops_without_discarding_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory)
            path.write_bytes(b'invalid SQLite data')
            pair=(path,Path(str(path)+'-journal'));before=fingerprint(pair)
            with self.assertRaises(RuntimeError):self.recover(path,output)
            self.assertEqual(fingerprint(pair),before)
            self.assertEqual(len(list(output.glob('sqlite-recovery-*'))),1)


if __name__=='__main__':unittest.main()
