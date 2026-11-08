import copy,json,os,signal,tempfile,time,unittest
from pathlib import Path
from contextlib import nullcontext
from hue_admin.storage_owner import recover_storage,launch_maintenance
from test_storage_maintenance import IO


class Journal:
    def __init__(self,state):self.state=copy.deepcopy(state);self.records=[]
    def load(self):return copy.deepcopy(self.state)
    def save(self,state):self.state=copy.deepcopy(state);self.records.append(copy.deepcopy(state))
    def exclusive(self):return nullcontext()


class RecoveryTests(unittest.TestCase):
    def state(self,phase='checking',clean=False):
        return {'schema':1,'phase':phase,'restoreVerified':False,'clean':clean}
    def test_interrupted_check_recovers_readonly_without_claiming_clean(self):
        io=IO();io.mounts={'opt':False,'storage':False};io.recovery_ready=lambda:True
        journal=Journal(self.state());result=recover_storage(io,journal)
        self.assertFalse(result['clean']);self.assertFalse(result['restoreVerified'])
        self.assertEqual(result['phase'],'unclean_readonly');self.assertTrue(all(io.ro.values()))
        self.assertNotIn('publish',io.calls);self.assertNotIn('start',io.calls)
    def test_interrupted_before_check_restores_rw_and_services(self):
        io=IO();io.mounts['opt']=False;io.recovery_ready=lambda:True
        journal=Journal(self.state('detaching'));result=recover_storage(io,journal)
        self.assertTrue(result['restoreVerified']);self.assertEqual(result['phase'],'aborted_restored')
        self.assertIn('start',io.calls);self.assertNotIn('publish',io.calls)
    def test_active_checker_or_device_change_forbids_recovery(self):
        for ready,same in ((False,True),(True,False)):
            io=IO();io.same=same;io.recovery_ready=lambda:ready
            with self.assertRaises(RuntimeError):recover_storage(io,Journal(self.state()))
            self.assertNotIn('mount:opt',io.calls);self.assertNotIn('start',io.calls)
    def test_recorded_clean_result_restores_rw_but_does_not_forge_missing_evidence(self):
        io=IO();io.recovery_ready=lambda:True;io.mounts={'opt':False,'storage':False}
        journal=Journal(self.state('restoring',True));result=recover_storage(io,journal)
        self.assertEqual(result['phase'],'aborted_restored');self.assertTrue(result['restoreVerified'])
        self.assertNotIn('publish',io.calls)
    def test_unknown_stop_receipt_remains_blocked(self):
        io=IO();io.recovery_ready=lambda:self.fail('unsafe readiness attempt')
        with self.assertRaisesRegex(RuntimeError,'STORAGE_OWNER_UNSAFE'):
            recover_storage(io,Journal(self.state('checker_stop_unknown')))
    def test_launcher_never_maps_aborted_or_readonly_to_success(self):
        from unittest.mock import patch
        io=IO();io.recovery_ready=lambda:True;journal=Journal(self.state())
        with patch('hue_admin.storage_owner.detach',return_value=10) as detach:
            self.assertEqual(launch_maintenance(io,journal),10)
            run,recover=detach.call_args.args
            with patch.object(journal,'load',side_effect=FileNotFoundError),patch('hue_admin.storage_owner.maintain',return_value={'phase':'aborted_restored','restoreVerified':True}):
                self.assertFalse(run())
            with patch('hue_admin.storage_owner.recover_storage',return_value={'phase':'unclean_readonly','restoreVerified':False}):
                self.assertFalse(recover())


@unittest.skipUnless(os.name=='posix' and os.geteuid()==0,'Native RAM owner')
class NativeOwnerTests(unittest.TestCase):
    def test_ram_journal_private_roundtrip_and_duplicate_lock_refusal(self):
        from hue_admin.storage_journal import RamJournal
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            root=Path(directory);root.chmod(0o700);journal=RamJournal(root)
            value={'schema':1,'phase':'prepared','clean':False,'restoreVerified':False}
            with journal.exclusive():
                journal.save(value);self.assertEqual(journal.load(),value)
                with self.assertRaisesRegex(RuntimeError,'STORAGE_MAINTENANCE_BUSY'):
                    with RamJournal(root).exclusive():self.fail('duplicate admitted')
            self.assertEqual((root/'maintenance.json').stat().st_mode&0o777,0o600)
            (root/'maintenance.json').chmod(0o644)
            with self.assertRaises(ValueError):journal.load()

    def test_killed_check_owner_recovers_readonly_without_success(self):
        from hue_admin.storage_journal import RamJournal
        from hue_admin.storage_owner import launch_maintenance
        from hue_admin.acceptance_journal import process_token
        with tempfile.TemporaryDirectory(dir='/tmp') as directory:
            root=Path(directory);root.chmod(0o700);journal=RamJournal(root)
            statefile=root/'fixture-mounts.json';statefile.write_text(json.dumps({'opt':True,'storage':True}))
            ready=root/'worker.json'
            retained=os.open(statefile,os.O_RDONLY)
            class Fake(IO):
                fd=retained
                def prepare_process(self):
                    # A real descriptor must survive detach into both children;
                    # per-child initialized state cannot come from the worker.
                    self.ready_device=os.fstat(self.fd).st_dev
                def validate(self):pass
                def mounted(self,name):return json.loads(statefile.read_text())[name]
                def readonly(self,name):return (root/('ro-'+name)).exists()
                def unmount(self,name):
                    value=json.loads(statefile.read_text());value[name]=False;statefile.write_text(json.dumps(value))
                def mount(self,name,readonly):
                    value=json.loads(statefile.read_text());value[name]=True;statefile.write_text(json.dumps(value))
                    if readonly:(root/('ro-'+name)).touch()
                def check(self):
                    ready.write_text(json.dumps(process_token(os.getpid())))
                    time.sleep(30);return {}
                def recovery_ready(self):return self.ready_device==os.fstat(self.fd).st_dev
            guardian=launch_maintenance(Fake(),journal,timeout=35)
            reaped=False
            try:
                deadline=time.monotonic()+5
                while not ready.exists() and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(ready.exists());token=json.loads(ready.read_text())
                self.assertEqual(process_token(token['pid']),token)
                self.assertEqual(int(Path(f"/proc/{token['pid']}/stat").read_text().rsplit(') ',1)[1].split()[1]),guardian)
                os.kill(token['pid'],signal.SIGKILL)
                deadline=time.monotonic()+8
                while True:
                    result=os.waitid(os.P_PID,guardian,os.WEXITED|os.WNOHANG|os.WNOWAIT)
                    if result is not None:break
                    if time.monotonic()>deadline:self.fail('guardian did not recover')
                    time.sleep(.02)
                os.waitpid(guardian,0);reaped=True
                self.assertNotEqual(result.si_status,0)
                value=journal.load();self.assertEqual(value['phase'],'unclean_readonly')
                self.assertFalse(value['restoreVerified']);self.assertFalse(value['clean'])
                self.assertTrue(all(json.loads(statefile.read_text()).values()))
                self.assertTrue(all((root/('ro-'+name)).exists() for name in ('opt','storage')))
            finally:
                if not reaped:os.kill(guardian,signal.SIGKILL);os.waitpid(guardian,0)
                os.close(retained)


if __name__=='__main__':unittest.main()
