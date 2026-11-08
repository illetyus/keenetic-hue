import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hue_agent.journal import Journal
from hue_agent.protocol import Command, ProtocolError
from test_core import payload


class JournalCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.temp.name)/'journal.sqlite')
        self.first = None
        records=[]
        for i in range(256):
            command = Command.parse(payload(commandId=str(i), sequence=i), 'session1')
            if i == 0:
                self.first = command
            records.append((command.command_id,command.fingerprint,'{"status":"verified"}',time.time()))
        # Seed existing history in one transaction; boundary calls below exercise reserve.
        # Hundreds of FULL fsyncs in setup tested USB flush latency, not capacity policy.
        with self.journal.db:
            self.journal.db.executemany('INSERT INTO commands VALUES (?,?,?,?)',records)
            self.journal.db.execute('INSERT INTO sequences VALUES (?,?,?)',('session1',255,time.time()))

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def test_257th_unique_command_busy_without_eviction(self):
        new = Command.parse(payload(commandId='257', sequence=257), 'session1')
        with self.assertRaisesRegex(ProtocolError, 'BUSY'):
            self.journal.reserve(new)
        self.assertEqual(self.journal.lookup(self.first)['status'], 'verified')
        self.assertEqual(self.journal.db.execute('SELECT count(*) FROM commands').fetchone()[0], 256)

    def test_duplicate_and_conflict_still_work_at_capacity(self):
        self.assertEqual(self.journal.reserve(self.first)['status'], 'verified')
        conflict = Command.parse(dict(self.first.payload, brightness=70), 'session1', allow_expired=True)
        with self.assertRaisesRegex(ProtocolError, 'COMMAND_ID_CONFLICT'):
            self.journal.reserve(conflict)

    def test_expired_record_frees_capacity(self):
        with patch('hue_agent.journal.time.monotonic', return_value=time.monotonic()+1801):
            new = Command.parse(payload(commandId='257', sequence=257), 'session1')
            self.assertIsNone(self.journal.reserve(new))
            self.assertEqual(self.journal.db.execute('SELECT count(*) FROM commands').fetchone()[0], 1)
