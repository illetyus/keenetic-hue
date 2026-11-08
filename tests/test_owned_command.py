import os,tempfile,time,sys,unittest
from pathlib import Path
from hue_admin.owned_command import run_owned


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Native owned command')
class OwnedCommandTests(unittest.TestCase):
    def test_timeout_kills_service_descendant_before_returning(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'late-stop'
            code="import os,time;pid=os.fork();time.sleep(.5);open("+repr(str(marker))+",'w').write('late')"
            with self.assertRaisesRegex(RuntimeError,'COMMAND_TIMEOUT'):
                run_owned([sys.executable,'-c',code],timeout=.1,cwd=directory)
            time.sleep(.6);self.assertFalse(marker.exists())
    def test_successful_leader_still_drains_descendant_before_returning(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'late-stop'
            code="import os,time;pid=os.fork();os._exit(0) if pid else None;time.sleep(.5);open("+repr(str(marker))+",'w').write('late')"
            value=run_owned([sys.executable,'-c',code],timeout=2,cwd=directory)
            time.sleep(.6);self.assertEqual(value['exitCode'],0);self.assertFalse(marker.exists())


if __name__=='__main__':unittest.main()
