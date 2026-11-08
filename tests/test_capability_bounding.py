import unittest
from unittest.mock import patch
from hue_admin import privileges


class BoundingSetTests(unittest.TestCase):
 def test_every_kernel_capability_is_dropped_and_read_back(self):
  bits={0,1,2};calls=[]
  def prctl(op,cap,*unused):
   calls.append((op,cap))
   if op==24:bits.discard(cap);return 0
   return int(cap in bits)
  self.assertTrue(privileges.drop_capability_bounding_set(prctl,lambda:'2\n'))
  self.assertEqual(bits,set())
  self.assertEqual([cap for op,cap in calls if op==24],[0,1,2])
  self.assertEqual(calls[-3:],[(23,0),(23,1),(23,2)])

 def test_invalid_cap_limit_never_calls_prctl(self):
  for value in ('-1','64','2.0','no','１２'):
   with self.subTest(value=value):
    self.assertFalse(privileges.drop_capability_bounding_set(lambda *a:self.fail('unexpected syscall'),lambda:value))

 def test_drop_readback_and_metadata_fail_closed(self):
  def denied(op,cap,*unused):return -1 if op==24 else 1
  def sticky(op,cap,*unused):return 0 if op==24 else 1
  def unreadable():raise OSError('fixture')
  self.assertFalse(privileges.drop_capability_bounding_set(denied,lambda:'1'))
  self.assertFalse(privileges.drop_capability_bounding_set(sticky,lambda:'1'))
  self.assertFalse(privileges.drop_capability_bounding_set(lambda *a:0,unreadable))

 def test_nonempty_or_missing_bounding_set_fails_final_check(self):
  prefix='CapEff: 0\nCapPrm: 0\nCapInh: 0\nCapAmb: 0\n'
  for suffix,expected in [('CapBnd: 0\n',True),('CapBnd: 1\n',False),('',False)]:
   with patch.object(privileges.Path,'read_text',return_value=prefix+suffix):
    self.assertEqual(privileges.capabilities_empty(),expected)

 def test_bounding_drop_precedes_uid_and_gid_changes(self):
  calls=[]
  class Ops:
   def setgroups(self,g):calls.append('groups')
   def setresgid(self,*g):calls.append('gid')
   def setresuid(self,*u):calls.append('uid')
   def getresgid(self):return (2001,)*3
   def getresuid(self):return (2000,)*3
   def getgroups(self):return []
  def bounding():calls.append('bounding');return True
  privileges.drop_identity(2000,2001,Ops(),lambda:True,lambda:True,bounding)
  self.assertEqual(calls,['bounding','groups','gid','uid'])
  calls.clear()
  with self.assertRaisesRegex(RuntimeError,'BOUNDING_SET_DROP_UNCONFIRMED'):
   privileges.drop_identity(2000,2001,Ops(),lambda:True,lambda:True,lambda:False)
  self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
