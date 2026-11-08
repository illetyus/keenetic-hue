import copy
import unittest
from hue_admin.storage_maintenance import maintain


class IO:
    def __init__(self):
        self.calls=[];self.mounts={'opt':True,'storage':True};self.ro={};self.same=True
        self.quiet=True;self.code='clean';self.fail=None
    def step(self,name):
        self.calls.append(name)
        if self.fail==name:raise OSError('fixture')
    def validate(self):self.step('validate')
    def independent(self):self.step('independent');return True
    def same_device(self):return self.same
    def mounted(self,name):return self.mounts[name]
    def readonly(self,name):return self.ro.get(name,False)
    def stop(self):self.step('stop');return True
    def quiescent(self):self.step('quiescent');return self.quiet
    def unmount(self,name):self.step('unmount:'+name);self.mounts[name]=False
    def mount(self,name,readonly):self.step('mount:'+name);self.mounts[name]=True;self.ro[name]=readonly
    def check(self):
        self.step('check')
        if self.code=='unclean':raise RuntimeError('STORAGE_CHECK_UNCLEAN')
        if self.code=='unknown':raise RuntimeError('FSCK_STOP_UNCONFIRMED')
        return {'verified':'fixture-only'}
    def publish(self,result):self.step('publish');return True
    def start(self):self.step('start');return True
    def healthy(self):self.step('healthy');return True


class MaintenanceTests(unittest.TestCase):
    def run_case(self,io,records):return maintain(io,lambda value:records.append(copy.deepcopy(value)))
    def test_clean_result_published_only_after_remount_then_services_verified(self):
        io=IO();records=[];result=self.run_case(io,records)
        self.assertEqual(result['phase'],'closed');self.assertTrue(result['restoreVerified'])
        self.assertEqual(io.calls[-5:],['mount:storage','mount:opt','publish','start','healthy'])
        self.assertEqual([v['phase'] for v in records],['prepared','stopping','detaching','checking','restoring','closed'])
    def test_live_usb_holder_blocks_check_and_restarts_baseline(self):
        io=IO();io.quiet=False;records=[]
        with self.assertRaisesRegex(RuntimeError,'STORAGE_HOLDERS_REMAIN'):self.run_case(io,records)
        self.assertNotIn('check',io.calls);self.assertNotIn('unmount:opt',io.calls)
        self.assertIn('start',io.calls);self.assertEqual(records[-1]['phase'],'aborted_restored')
    def test_partial_detach_failure_restores_without_checking(self):
        io=IO();io.fail='unmount:storage';records=[]
        with self.assertRaises(OSError):self.run_case(io,records)
        self.assertNotIn('check',io.calls);self.assertTrue(all(io.mounts.values()))
        self.assertIn('start',io.calls)
    def test_unclean_check_restores_readonly_and_never_starts_or_publishes(self):
        io=IO();io.code='unclean';records=[]
        with self.assertRaises(RuntimeError):self.run_case(io,records)
        self.assertEqual(io.ro,{'storage':True,'opt':True})
        self.assertNotIn('start',io.calls);self.assertNotIn('publish',io.calls)
        self.assertEqual(records[-1]['phase'],'unclean_readonly')
    def test_unknown_checker_stop_never_remounts_or_starts(self):
        io=IO();io.code='unknown';records=[]
        with self.assertRaisesRegex(RuntimeError,'FSCK_STOP_UNCONFIRMED'):self.run_case(io,records)
        self.assertNotIn('mount:opt',io.calls);self.assertNotIn('start',io.calls)
        self.assertEqual(records[-1]['phase'],'checker_stop_unknown')
    def test_unknown_stop_ownership_is_preserved_when_failure_record_cannot_write(self):
        io=IO();owned=object();problem=RuntimeError('FSCK_STOP_UNCONFIRMED');problem.ownedProcess=owned
        def check():raise problem
        def save(value):
            if value['phase']=='checker_stop_unknown':raise OSError('record unavailable')
        io.check=check
        with self.assertRaisesRegex(RuntimeError,'FSCK_STOP_UNCONFIRMED') as caught:maintain(io,save)
        self.assertIs(caught.exception,problem);self.assertIs(caught.exception.ownedProcess,owned)
        self.assertIsInstance(caught.exception.recordError,OSError)
        self.assertNotIn('mount:opt',io.calls)
    def test_unknown_service_command_stop_blocks_conflicting_recovery_start(self):
        io=IO();records=[];problem=RuntimeError('COMMAND_STOP_UNCONFIRMED');problem.ownedProcess=object()
        io.stop=lambda:(_ for _ in ()).throw(problem)
        with self.assertRaisesRegex(RuntimeError,'COMMAND_STOP_UNCONFIRMED') as caught:self.run_case(io,records)
        self.assertIs(caught.exception,problem)
        self.assertNotIn('start',io.calls);self.assertNotIn('mount:opt',io.calls)
        self.assertEqual(records[-1]['phase'],'checker_stop_unknown')
    def test_record_failure_before_stop_prevents_all_mutations(self):
        io=IO()
        with self.assertRaises(OSError):maintain(io,lambda _:(_ for _ in ()).throw(OSError()))
        self.assertNotIn('stop',io.calls);self.assertNotIn('unmount:opt',io.calls)
    def test_restoring_record_failure_still_recovers_mounts_but_cannot_close(self):
        io=IO();records=[]
        def save(value):
            records.append(copy.deepcopy(value))
            if value['phase']=='restoring':raise OSError('RAM record unavailable')
        with self.assertRaises(OSError):maintain(io,save)
        self.assertTrue(all(io.mounts.values()));self.assertIn('start',io.calls)
        self.assertNotIn('closed',[r['phase'] for r in records])
    def test_device_change_during_check_refuses_remount_or_publish(self):
        io=IO();records=[]
        def check():io.same=False;return {}
        io.check=check
        with self.assertRaisesRegex(RuntimeError,'STORAGE_DEVICE_CHANGED'):self.run_case(io,records)
        self.assertNotIn('mount:opt',io.calls);self.assertNotIn('publish',io.calls)
    def test_failed_remount_does_not_start_services(self):
        io=IO();io.fail='mount:storage';records=[]
        with self.assertRaises(OSError):self.run_case(io,records)
        self.assertNotIn('start',io.calls);self.assertNotIn('publish',io.calls)


if __name__=='__main__':unittest.main()
