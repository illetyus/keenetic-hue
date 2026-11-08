"""Real subprocess interruptions; every database and effect is disposable."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from hue_admin.agent_snapshot import snapshot_agent_database
from hue_agent.journal import Journal
from hue_agent.protocol import Command
from test_core import payload

MAGIC = bytes.fromhex('d9d505f920a163d7')
ROOT = Path(__file__).resolve().parents[1]
CRASH = r'''
import os,sqlite3,sys
from pathlib import Path
from hue_agent.journal import Journal
from hue_agent.protocol import Command
from test_core import payload
path=Path(sys.argv[1]);stage=sys.argv[2]
if stage=='hot':
 db=sqlite3.connect(path)
 db.execute('PRAGMA synchronous=FULL');db.execute('PRAGMA cache_size=4')
 db.execute('PRAGMA cache_spill=ON');db.execute('BEGIN IMMEDIATE')
 db.execute('UPDATE commands SET result=?',('z'*4096,))
 assert Path(str(path)+'-journal').read_bytes()[:8]==bytes.fromhex('d9d505f920a163d7')
 os._exit(73)
journal=Journal(path)
if stage=='before_reserve_commit':
 journal.db.close()
 class CutBeforeCommit(sqlite3.Connection):
  def __exit__(self,*args):os._exit(73)
 journal.db=sqlite3.connect(path,factory=CutBeforeCommit)
command=Command.parse(payload(commandId='interrupted',sequence=100,expiresAt=1),'session1',allow_expired=True)
journal.reserve(command)
if stage=='after_effect':path.with_suffix('.effect').write_text('one simulated BLE write')
os._exit(73)
'''


def interrupt(path, stage):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(ROOT), str(ROOT/'tests'))),
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', CRASH, str(path), stage],
                            env=env, capture_output=True, timeout=15)
    if result.returncode != 73:
        raise AssertionError('Crash fixture did not reach its exit point: '+result.stderr.decode(errors='replace'))


def hot_fixture(directory):
    path = Path(directory)/'commands.sqlite'
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('CREATE TABLE commands (id TEXT PRIMARY KEY, hash TEXT, result TEXT, created REAL)')
        db.execute('CREATE TABLE sequences (session TEXT PRIMARY KEY, last INTEGER, created REAL)')
        db.executemany('INSERT INTO commands VALUES (?,?,?,?)',
                       [(str(n), 'seed', 'x'*4096, 1) for n in range(128)])
        db.execute("INSERT INTO sequences VALUES ('committed-session',99,1)")
        db.commit()
    interrupt(path, 'hot')
    assert Path(str(path)+'-journal').read_bytes()[:8] == MAGIC
    return path


def fingerprint(paths):
    return [(hashlib.sha256(p.read_bytes()).digest(), p.stat().st_mtime_ns) for p in paths]


class CrashJournalTests(unittest.TestCase):
    def test_readonly_fails_unchanged_and_writable_copy_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = hot_fixture(directory); side = Path(str(path)+'-journal')
            before = fingerprint((path, side))
            with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
                    db.execute('SELECT name FROM sqlite_master').fetchall()
            self.assertEqual(fingerprint((path, side)), before)
            copy = Path(directory)/'copy.sqlite'
            shutil.copyfile(path, copy);shutil.copyfile(side, Path(str(copy)+'-journal'))
            with closing(sqlite3.connect(copy.as_uri()+'?mode=rw', uri=True)) as db:
                self.assertEqual(db.execute('PRAGMA quick_check').fetchone(), ('ok',))
                self.assertEqual(db.execute('SELECT count(*) FROM commands WHERE result=?', ('x'*4096,)).fetchone()[0],128)
                self.assertEqual(db.execute('SELECT last FROM sequences').fetchone()[0],99)
            self.assertFalse(Path(str(copy)+'-journal').exists())
            self.assertEqual(fingerprint((path, side)), before)

    def test_reservation_crash_points_never_replay_an_uncertain_effect(self):
        for stage in ('before_reserve_commit','after_reserve','after_effect'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'commands.sqlite'
                Journal(path).close();interrupt(path,stage)
                journal=Journal(path)
                try:
                    command=Command.parse(payload(commandId='interrupted',sequence=100,expiresAt=1),'session1',allow_expired=True)
                    cached=journal.reserve(command)
                    if stage=='before_reserve_commit':self.assertIsNone(cached)
                    else:self.assertEqual(cached['errorCode'],'OUTCOME_UNKNOWN')
                    self.assertEqual(journal.db.execute('SELECT last FROM sequences').fetchone()[0],100)
                    effect=path.with_suffix('.effect')
                    self.assertEqual(effect.exists(),stage=='after_effect')
                    if effect.exists():self.assertEqual(effect.read_text(),'one simulated BLE write')
                    # A duplicate remains cached; no dispatcher is invoked by this test.
                    self.assertEqual(journal.reserve(command)['errorCode'],'OUTCOME_UNKNOWN')
                finally:journal.close()


@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,
                     'Requires native Linux root for real agent-UID boundary')
class StoppedSnapshotCrashTests(unittest.TestCase):
    def fixture(self, directory):
        base=Path(directory);base.chmod(0o755)
        agent=base/'agent';agent.mkdir(mode=0o700)
        path=hot_fixture(agent)
        for item in agent.iterdir():item.chmod(0o600);os.chown(item,61001,61001)
        os.chown(agent,61001,61001)
        output=base/'output';output.mkdir(mode=0o700)
        return path,output

    def test_current_snapshot_rejects_hot_journal_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory);pair=(path,Path(str(path)+'-journal'))
            before=fingerprint(pair)
            with self.assertRaisesRegex(RuntimeError,'AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA'):
                snapshot_agent_database(path,61001,61001,output_dir=output)
            self.assertEqual(fingerprint(pair),before)

    def test_regression_stopped_snapshot_should_recover_hot_journal(self):
        # Recovery is an explicit internal authorization from stopped checkpoint.
        with tempfile.TemporaryDirectory() as directory:
            path,output=self.fixture(directory)
            data=snapshot_agent_database(path,61001,61001,output_dir=output,recover_hot=True)
            restored=output/'restored.sqlite';restored.write_bytes(data)
            with closing(sqlite3.connect(restored)) as db:
                self.assertEqual(db.execute('PRAGMA quick_check').fetchone(),('ok',))
                self.assertEqual(db.execute('SELECT count(*) FROM commands').fetchone()[0],128)
                self.assertEqual(db.execute('SELECT last FROM sequences').fetchone()[0],99)


if __name__=='__main__':unittest.main()
