import copy,json,os,tempfile,unittest,uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from hue_admin.controlled_acceptance import run_controlled,ManagedRelease


class AdapterTests(unittest.TestCase):
 def test_durable_record_contains_baseline_needed_after_process_loss(self):
  from test_acceptance_recovery import Adapter
  from hue_admin.acceptance_recovery import validate_snapshot
  adapter=Adapter();adapter.release='0.2.0-rc.5';adapter.preflight=lambda _:None
  records=[]
  def supervised(worker,recover,record,operation_id,timeout):
   value={'schema':1,'operationId':operation_id,'phase':'prepared','restoreVerified':False,
          'supervisorPid':os.getpid(),'outcome':None}
   record(value)
   return value
  with patch('hue_admin.controlled_acceptance.supervise',side_effect=supervised):
   result=run_controlled(adapter,'0.2.0-rc.5','0.2.0-rc.7',records.append,str(uuid.uuid4()))
  self.assertEqual(validate_snapshot(records[0]),result)
  self.assertEqual(result['baselineFingerprints'],adapter.fingerprints())
  self.assertEqual(result['baselinePhysical'],adapter.physical())
 def test_storage_gate_blocks_before_any_archive_work(self):
  adapter=ManagedRelease('/not-a-live-root','/not-a-live-root/backups/candidates/test.huerel','0'*64,observe=lambda:None,storage_ready=lambda:False)
  with patch('hue_admin.release_commands.archive_release') as read_archive:
   with self.assertRaisesRegex(ValueError,'STORAGE_ACCEPTANCE_REQUIRED'):adapter.preflight('candidate')
   read_archive.assert_not_called()
 def test_normal_release_library_is_used_without_nested_cli(self):
  adapter=ManagedRelease('/fixture','/fixture/backups/candidates/test.huerel','0'*64,observe=lambda:None,storage_ready=lambda:False)
  with patch('hue_admin.release_commands.execute',return_value={'fixture':True}) as execute:
   self.assertEqual(adapter.update('candidate'),{'fixture':True});adapter.rollback();adapter.recover();adapter.start()
   self.assertEqual([call.args[0] for call in execute.call_args_list],['update','rollback','recover','internal-service'])
   self.assertEqual(execute.call_args_list[0].args[1],adapter.archive)
   self.assertEqual(execute.call_args_list[-1].kwargs['boot_action'],'start')


@unittest.skipUnless(os.name=='posix' and hasattr(os,'fork'),'Native POSIX controller')
class ControllerTests(unittest.TestCase):
 def exercise(self,mode='success'):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'release';path.write_text('baseline');events=[];records=[]
   class Adapter:
    @contextmanager
    def exclusive(self):
     events.append('lock')
     try:yield
     finally:events.append('unlock')
    def preflight(self,candidate):
     if mode=='preflight_fail':raise ValueError('fixture')
    def selected(self):return path.read_text()
    def healthy(self,release):return self.selected()==release
    def fingerprints(self):return {'master':'changed' if mode=='master_changed' and self.selected()=='candidate' else 'stable'}
    def physical(self):return {'on':False,'brightnessRaw':50}
    def update(self,candidate):
     path.write_text(candidate)
     if mode=='update_fails':raise RuntimeError('fixture')
    def candidate_checks(self):
     if mode=='unrelated':path.write_text('unrelated');return False
     return mode!='checks_fail'
    def recover(self):return None
    def start(self):return None
    def rollback(self):
     if mode=='rollback_fails':raise RuntimeError('fixture')
     path.write_text('baseline')
   adapter=Adapter()
   def record(v):
    self.assertEqual(events[-1],'lock');records.append(copy.deepcopy(v))
   if mode=='preflight_fail':
    with self.assertRaises(ValueError):run_controlled(adapter,'baseline','candidate',record,str(uuid.uuid4()),2)
    self.assertEqual(records,[]);self.assertEqual(path.read_text(),'baseline');return
   result=run_controlled(adapter,'baseline','candidate',record,str(uuid.uuid4()),2)
   self.assertEqual(events,['lock','unlock'])
   self.assertEqual(records[-1],result)
   return result,path.read_text()

 def test_candidate_verified_then_baseline_restored(self):
  result,selected=self.exercise();self.assertEqual(selected,'baseline')
  self.assertEqual(result['outcome'],'completed');self.assertTrue(result['restoreVerified'])
  self.assertEqual(result['baselineRelease'],'baseline');self.assertEqual(result['candidateRelease'],'candidate')
 def test_preflight_failure_never_starts_worker(self):self.exercise('preflight_fail')
 def test_partial_update_and_check_failure_still_restore(self):
  for mode in ('update_fails','checks_fail','master_changed'):
   with self.subTest(mode=mode):
    result,selected=self.exercise(mode);self.assertEqual(selected,'baseline')
    self.assertEqual(result['outcome'],'failed');self.assertTrue(result['restoreVerified'])
 def test_rollback_failure_is_not_accepted(self):
  result,selected=self.exercise('rollback_fails');self.assertEqual(selected,'candidate')
  self.assertEqual(result['phase'],'recovery_failed');self.assertFalse(result['restoreVerified'])
 def test_unrelated_selection_is_not_rolled_back(self):
  result,selected=self.exercise('unrelated');self.assertEqual(selected,'unrelated')
  self.assertEqual(result['phase'],'recovery_failed');self.assertFalse(result['restoreVerified'])


if __name__=='__main__':unittest.main()
