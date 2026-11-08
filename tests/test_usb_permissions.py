import stat
from types import SimpleNamespace
import unittest

from hue_admin.usb_permissions import change_node_permissions


class DeviceOps:
    def __init__(self,**changes):
        self.info=SimpleNamespace(st_mode=stat.S_IFCHR|0o640,st_uid=0,st_gid=0,st_nlink=1,st_dev=9,st_ino=12,st_rdev=55)
        vars(self.info).update(changes);self.calls=[]
    def lstat(self,path):return self.info
    def open(self,path,flags):self.calls.append(('open',path));return 17
    def close(self,fd):self.calls.append(('close',fd))
    def fstat(self,fd):return SimpleNamespace(**vars(self.info))
    def major(self,value):return 189
    def minor(self,value):return 3
    def fchown(self,fd,uid,gid):self.calls.append(('chown',fd,uid,gid));self.info.st_uid=uid;self.info.st_gid=gid
    def fchmod(self,fd,mode):self.calls.append(('chmod',fd,mode));self.info.st_mode=stat.S_IFCHR|mode


class UsbPermissionTests(unittest.TestCase):
    target={'path':'/dev/bus/usb/001/004','major':189,'minor':3,'sysfsName':'1-2.4'}

    def test_dry_run_validates_opened_node_without_changing_permissions(self):
        ops=DeviceOps()
        change_node_permissions(self.target,61001,lambda:True,check_only=True,ops=ops)
        self.assertEqual(ops.calls,[('open',self.target['path']),('close',17)])
        with self.assertRaises(ValueError):
            change_node_permissions(self.target,61001,lambda:True,check_only=True,ops=DeviceOps(st_uid=61000))

    def test_only_checked_descriptor_receives_service_group(self):
        ops=DeviceOps()
        change_node_permissions(self.target,61001,lambda:True,ops=ops)
        self.assertIn(('chown',17,0,61001),ops.calls)
        self.assertIn(('chmod',17,0o660),ops.calls)
        self.assertEqual([v for v in ops.calls if v[0]=='open'],[('open',self.target['path'])])

    def test_wrong_owner_type_group_or_link_is_rejected(self):
        for changes in ({'st_uid':61000},{'st_gid':61000},{'st_mode':stat.S_IFLNK|0o777},
                        {'st_mode':stat.S_IFCHR|0o666},{'st_nlink':2}):
            with self.subTest(changes=changes):
                ops=DeviceOps(**changes)
                with self.assertRaises(ValueError):change_node_permissions(self.target,61001,lambda:True,ops=ops)
                self.assertFalse(any(call[0]=='chown' for call in ops.calls))

    def test_changed_identity_rolls_back_same_fd_not_new_path(self):
        ops=DeviceOps();checks=iter((True,False))
        with self.assertRaises(ValueError):change_node_permissions(self.target,61001,lambda:next(checks),ops=ops)
        self.assertEqual((ops.info.st_uid,ops.info.st_gid,stat.S_IMODE(ops.info.st_mode)),(0,0,0o640))
        self.assertIn(('chown',17,0,0),ops.calls)

    def test_restore_returns_only_pinned_node_to_original_baseline(self):
        ops=DeviceOps(st_gid=61001,st_mode=stat.S_IFCHR|0o660)
        change_node_permissions(self.target,61001,lambda:True,restore=True,ops=ops)
        self.assertEqual((ops.info.st_gid,stat.S_IMODE(ops.info.st_mode)),(0,0o640))
