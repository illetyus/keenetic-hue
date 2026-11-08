import tempfile,time,unittest
from pathlib import Path
from hue_agent.journal import Journal
from hue_agent.protocol import Command,DEVICE_ID
from unittest.mock import patch

class QueryCapacityTests(unittest.TestCase):
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
