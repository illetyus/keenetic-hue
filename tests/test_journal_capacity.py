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

    def test_session_sequences_bounded_and_pruned(self):
        from hue_agent.protocol import DEVICE_ID
        with tempfile.TemporaryDirectory() as tmp:
            j = Journal(Path(tmp)/'journal.sqlite')
            try:
                base_mono = j.mono_anchor
                for i in range(500):
                    cmd = Command.parse(
                        {'commandId': f'q-{i}', 'deviceId': DEVICE_ID, 'sessionId': f'sess-{i}',
                         'sequence': 1, 'expiresAt': time.time()+5, 'operation': 'query'},
                        f'sess-{i}',
                    )
                    self.assertIsNone(j.reserve_fast(cmd))
                self.assertLessEqual(len(j._session_sequences), 256)
                self.assertEqual(len(j._session_seq_ts), len(j._session_sequences))
                self.assertIn('sess-499', j._session_sequences)

                with patch('hue_agent.journal.time.monotonic', return_value=base_mono + 1801):
                    # Active session sess-499 still rejects stale sequence=1 while pruning inactive ones.
                    stale_active = Command.parse(
                        {'commandId': 'q-stale', 'deviceId': DEVICE_ID, 'sessionId': 'sess-499',
                         'sequence': 1, 'expiresAt': time.time()+5, 'operation': 'query'},
                        'sess-499',
                    )
                    with self.assertRaisesRegex(ProtocolError, 'STALE_SEQUENCE'):
                        j.reserve_fast(stale_active)
                    self.assertEqual(list(j._session_sequences.keys()), ['sess-499'])
                    self.assertEqual(list(j._session_seq_ts.keys()), ['sess-499'])
            finally:
                j.close()

    def test_pre_ntp_future_created_clamped_on_startup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp)/'journal.sqlite'
            seed = Journal(db_path)
            try:
                future_ts = time.time() + 100000
                records = []
                for i in range(256):
                    cmd = Command.parse(payload(commandId=f'pre-{i}', sessionId='pre-ntp', sequence=i), 'pre-ntp')
                    records.append((cmd.command_id, cmd.fingerprint, '{"status":"verified"}', future_ts))
                with seed.db:
                    seed.db.executemany('INSERT INTO commands VALUES (?,?,?,?)', records)
                    seed.db.execute('INSERT INTO sequences VALUES (?,?,?)', ('pre-ntp', 255, future_ts))
            finally:
                seed.close()

            reopened = Journal(db_path)
            try:
                now = reopened.retention_now()
                self.assertEqual(len(reopened._mem_writes), 256)
                self.assertTrue(all(v[2] <= now for v in reopened._mem_writes.values()))

                with patch('hue_agent.journal.time.monotonic', return_value=reopened.mono_anchor + 1801):
                    fast_cmd = Command.parse(payload(commandId='after-ntp-fast', sessionId='pre-ntp', sequence=256), 'pre-ntp')
                    self.assertIsNone(reopened.reserve_fast(fast_cmd))
                    reopened.flush()

                    sync_cmd = Command.parse(payload(commandId='after-ntp-sync', sessionId='pre-ntp', sequence=257), 'pre-ntp')
                    self.assertIsNone(reopened.reserve(sync_cmd))
                    self.assertEqual(reopened.db.execute('SELECT count(*) FROM commands').fetchone()[0], 2)
            finally:
                reopened.close()
