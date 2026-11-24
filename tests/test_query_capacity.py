import sqlite3,tempfile,time,unittest
from pathlib import Path
from hue_agent.journal import Journal
from hue_agent.protocol import Command,DEVICE_ID,ProtocolError
from unittest.mock import patch

class QueryCapacityTests(unittest.TestCase):
    def test_journal_uses_persist_mode_and_query_never_writes_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'journal.sqlite'
            j=Journal(path)
            try:
                self.assertEqual(j.db.execute('PRAGMA journal_mode').fetchone()[0].lower(),'persist')
                self.assertEqual(j.db.execute('PRAGMA synchronous').fetchone()[0],2)
                q=Command.parse({'commandId':'q1','deviceId':DEVICE_ID,'sessionId':'s1','sequence':10,'expiresAt':time.time()+5,'operation':'query'},'s1')
                self.assertIsNone(j.reserve(q))
                j.finish(q,{'commandId':'q1','status':'verified'})
                self.assertEqual(j.db.execute('SELECT count(*) FROM commands').fetchone()[0],0)
                with self.assertRaisesRegex(ProtocolError,'COMMAND_ID_CONFLICT'):
                    j.reserve(Command.parse({'commandId':'q1','deviceId':DEVICE_ID,'sessionId':'s1','sequence':11,'expiresAt':time.time()+5,'operation':'set','on':True},'s1'))
                with self.assertRaisesRegex(ProtocolError,'STALE_SEQUENCE'):
                    j.reserve(Command.parse({'commandId':'s2','deviceId':DEVICE_ID,'sessionId':'s1','sequence':10,'expiresAt':time.time()+5,'operation':'set','on':True},'s1'))
                s=Command.parse({'commandId':'s2','deviceId':DEVICE_ID,'sessionId':'s1','sequence':11,'expiresAt':time.time()+5,'operation':'set','on':True},'s1')
                self.assertIsNone(j.reserve(s))
                j.finish(s,{'commandId':'s2','status':'verified'})
                ro=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
                try:
                    self.assertEqual(ro.execute('SELECT count(*) FROM commands').fetchone()[0],1)
                finally:
                    ro.close()
            finally:
                j.close()
    def test_wall_clock_jump_never_erases_current_sequence_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=Journal(Path(tmp)/'journal.sqlite')
            try:
                def command(n):return Command.parse({'commandId':str(n),'deviceId':DEVICE_ID,'sessionId':'fixture','sequence':n,'expiresAt':time.time()+5,'operation':'set','on':False},'fixture')
                old=command(1);j.reserve(old);j.finish(old,{'status':'verified'})
                with patch('hue_agent.journal.time.time',return_value=time.time()+3600):j.reserve(command(2))
                self.assertEqual(j.reserve(old),{'status':'verified'})
            finally:j.close()
    def test_queries_do_not_fill_write_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            j=Journal(Path(tmp)/'journal.sqlite')
            try:
                for n in range(300):
                    command=Command.parse({'commandId':str(n),'deviceId':DEVICE_ID,'sessionId':'fixture','sequence':n,'expiresAt':time.time()+5,'operation':'query'},'fixture')
                    self.assertIsNone(j.reserve(command));j.finish(command,{'status':'verified'})
                write=Command.parse({'commandId':'write','deviceId':DEVICE_ID,'sessionId':'fixture','sequence':301,'expiresAt':time.time()+5,'operation':'set','on':False},'fixture')
                self.assertIsNone(j.reserve(write))
                self.assertLessEqual(j.db.execute('SELECT count(*) FROM commands').fetchone()[0],65)
            finally:j.close()
