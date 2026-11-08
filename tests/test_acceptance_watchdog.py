import copy,os,tempfile,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from hue_admin.acceptance_watchdog import supervise


class InputTests(unittest.TestCase):
 def test_parent_death_guard_fails_closed_and_rechecks_parent(self):
  from hue_admin.acceptance_watchdog import guard_parent
  calls=[]
  self.assertFalse(guard_parent(42,prctl=lambda *args:-1,parent=lambda:42))
  self.assertFalse(guard_parent(42,prctl=lambda *args:0,parent=lambda:1))
  self.assertTrue(guard_parent(42,prctl=lambda *args:calls.append(args) or 0,parent=lambda:42))
  self.assertEqual(calls,[(1,9,0,0,0)])
 def test_invalid_inputs_never_start_or_recover(self):
  def forbidden(*args):self.fail('side effect before validation')
  for timeout in (0,-1,True,float('nan'),float('inf'),901):
   with self.subTest(timeout=timeout),self.assertRaises(ValueError):
    supervise(forbidden,forbidden,forbidden,str(uuid.uuid4()),timeout)
  with self.assertRaises(ValueError):supervise(forbidden,forbidden,forbidden,'../bad',1)


@unittest.skipUnless(os.name=='posix' and hasattr(os,'fork'),'Native POSIX fixture')
class NativeTests(unittest.TestCase):
 def run_guard(self,worker,recover=lambda:True,timeout=2,record=None):
  records=[]
  def save(v):records.append(copy.deepcopy(v))
  operation=str(uuid.uuid4())
  result=supervise(worker,recover,record or save,operation,timeout)
  return result,records,operation

 def test_success_has_same_operation_and_verified_restore(self):
  result,records,operation=self.run_guard(lambda:None)
  self.assertEqual([r['phase'] for r in records],['prepared','running','restoring','closed'])
  self.assertTrue(all(r['operationId']==operation for r in records))
  self.assertEqual(result['outcome'],'completed');self.assertTrue(result['restoreVerified'])

 def test_failed_worker_is_not_success_even_if_restored(self):
  def worker():raise RuntimeError('private fixture detail')
  result,records,_=self.run_guard(worker)
  self.assertEqual(result['outcome'],'failed');self.assertTrue(result['restoreVerified'])
  self.assertNotIn('private fixture detail',str(records))

 def test_timeout_kills_worker_and_grandchild_before_restore(self):
  with tempfile.TemporaryDirectory() as directory:
   marker=Path(directory)/'late-write'
   def worker():
    if os.fork()==0:
     time.sleep(.5);marker.write_text('unsafe');os._exit(0)
    time.sleep(30)
   result,_,_=self.run_guard(worker,timeout=.1)
   self.assertEqual(result['outcome'],'timed_out')
   time.sleep(.6);self.assertFalse(marker.exists());self.assertTrue(result['restoreVerified'])

 def test_finish_still_stops_descendants_before_restore(self):
  with tempfile.TemporaryDirectory() as directory:
   marker=Path(directory)/'late-write'
   def worker():
    if os.fork()==0:
     time.sleep(.5);marker.write_text('unsafe');os._exit(0)
   result,_,_=self.run_guard(worker)
   time.sleep(.6);self.assertFalse(marker.exists());self.assertEqual(result['outcome'],'completed')

 def test_recovery_failure_is_retryable_not_closed(self):
  for recover in (lambda:False,lambda:(_ for _ in ()).throw(OSError('secret'))):
   result,records,_=self.run_guard(lambda:None,recover)
   self.assertEqual(result['phase'],'recovery_failed');self.assertFalse(result['restoreVerified'])
   self.assertNotIn('secret',str(records))

 def test_prepare_receipt_failure_prevents_worker(self):
  def forbidden():self.fail('worker started')
  def save(_):raise OSError('disk full')
  with self.assertRaises(OSError):self.run_guard(forbidden,record=save)

 def test_final_receipt_failure_is_propagated(self):
  phases=[]
  def save(v):
   phases.append(v['phase'])
   if v['phase']=='closed':raise OSError('disk full')
  with self.assertRaises(OSError):self.run_guard(lambda:None,record=save)
  self.assertEqual(phases,['prepared','running','restoring','closed'])

 def test_group_inspection_failure_records_failure_without_recovery(self):
  records=[];recovery=[]
  with patch('hue_admin.acceptance_watchdog._live_group',side_effect=OSError('private detail')):
   with self.assertRaisesRegex(RuntimeError,'WORKER_STOP_UNCONFIRMED'):
    supervise(lambda:None,lambda:recovery.append(True),lambda v:records.append(copy.deepcopy(v)),str(uuid.uuid4()),2)
  self.assertEqual(recovery,[])
  self.assertEqual(records[-1]['phase'],'owner_not_stopped')
  self.assertNotIn('private detail',str(records))

 def test_running_receipt_failure_never_releases_worker(self):
  with tempfile.TemporaryDirectory() as directory:
   marker=Path(directory)/'mutation'
   def save(v):
    if v['phase']=='running':raise OSError('fixture')
   with self.assertRaises(OSError):self.run_guard(lambda:marker.write_text('bad'),record=save)
   self.assertFalse(marker.exists())


if __name__=='__main__':unittest.main()
