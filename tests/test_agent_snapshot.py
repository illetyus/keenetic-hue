"""Native privilege-separated SQLite snapshots; all data is disposable."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from hue_admin.agent_snapshot import snapshot_agent_database


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'Native UID/resource boundary')
class AgentSnapshotTests(unittest.TestCase):
    def fixture(self,directory):
        base=Path(directory);base.chmod(0o755)
        data=base/'agent';data.mkdir(mode=0o700);os.chown(data,61001,61001)
        output=base/'private';output.mkdir(mode=0o700)
        path=data/'commands.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
            db.execute('CREATE TABLE sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)')
            db.execute("INSERT INTO commands VALUES ('fixture','hash','{}',1)")
        path.chmod(0o600);os.chown(path,61001,61001)
        return path,output

    def test_snapshot_contains_data_and_has_exact_application_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory)
            data=snapshot_agent_database(path,61001,61001,output_dir=output)
            self.assertTrue(data.startswith(b'SQLite format 3\x00'))
            copy=output/'checked.sqlite';copy.write_bytes(data)
            with sqlite3.connect(copy) as db:
                self.assertEqual(db.execute('SELECT id FROM commands').fetchall(),[('fixture',)])

    def test_root_owned_source_is_never_parsed_as_agent_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory);os.chown(path,0,0)
            with self.assertRaisesRegex(RuntimeError,'AGENT_DATABASE_SNAPSHOT_FAILED:IDENTITY'):
                snapshot_agent_database(path,61001,61001,output_dir=output)

    def test_unexpected_schema_is_rejected_by_unprivileged_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory)
            with sqlite3.connect(path) as db:db.execute('CREATE TABLE unexpected(secret TEXT)')
            with self.assertRaisesRegex(RuntimeError,'AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA'):
                snapshot_agent_database(path,61001,61001,output_dir=output)

    def test_nonprivate_parent_output_is_rejected_before_spawning(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory);output.chmod(0o755)
            with self.assertRaises(ValueError):snapshot_agent_database(path,61001,61001,output_dir=output)
