import unittest

from hue_admin.usb_policy import target_for_usb,pinned_transport


class UsbPolicyTests(unittest.TestCase):
    policy={'vendor':'050d','product':'065a','serial':'AABBCCDDEEFF'}

    def device(self,**changes):
        return {'vendor':'050d','product':'065a','serial':'AABBCCDDEEFF',
                'bus':1,'device':4,'sysfsName':'1-2.4',**changes}

    def test_exact_adapter_produces_fixed_device_node_and_rdev(self):
        target=target_for_usb([self.device()],self.policy)
        self.assertEqual(target,{'path':'/dev/bus/usb/001/004','major':189,'minor':3,'sysfsName':'1-2.4'})

    def test_transport_pins_serial_instead_of_first_vid_pid_match(self):
        self.assertEqual(pinned_transport(self.policy),'usb:050d:065a/AABBCCDDEEFF!')
        for serial in ('*','AABBCCDDEEFF#0','AABBCCDDEEFF/other','AABBCCDDEEFF+sco=0'):
            with self.subTest(serial=serial),self.assertRaises(ValueError):
                pinned_transport({**self.policy,'serial':serial})

    def test_other_vid_pid_or_serial_cannot_select_a_node(self):
        for changes in ({'vendor':'1234'},{'product':'1234'},{'serial':'001122334455'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                target_for_usb([self.device(**changes)],self.policy)

    def test_multiple_matching_serials_are_not_resolved_by_first_match(self):
        with self.assertRaises(ValueError):
            target_for_usb([self.device(),self.device(device=5,sysfsName='1-2.3')],self.policy)

    def test_removed_device_is_not_a_wildcard(self):
        with self.assertRaises(ValueError):target_for_usb([],self.policy)

    def test_invalid_bus_or_device_cannot_form_paths(self):
        for value in (0,-1,128,'004',True,'../../etc/passwd'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                target_for_usb([self.device(device=value)],self.policy)
        for value in (0,-1,256,'1',True):
            with self.subTest(value=value),self.assertRaises(ValueError):
                target_for_usb([self.device(bus=value)],self.policy)

    def test_interfaces_and_paths_are_never_device_names(self):
        for name in ('1-2.4:1.0','../1-2','/sys/devices/1-2','1-2\n','2-2'):
            with self.subTest(name=name),self.assertRaises(ValueError):
                target_for_usb([self.device(sysfsName=name)],self.policy)

    def test_policy_cannot_be_relaxed_or_redirected(self):
        for policy in ({'vendor':'050d','product':'065a'},
                       {**self.policy,'serial':'*'},
                       {**self.policy,'vendor':'*'},
                       {**self.policy,'path':'/dev/bus/usb/001/001'}):
            with self.subTest(policy=policy),self.assertRaises(ValueError):
                target_for_usb([self.device()],policy)
