import unittest
from hue_admin.storage_live import validate_mount_plan,validate_listener,verify_target_resolution


class LivePlanTests(unittest.TestCase):
    def plan(self):
        return {'device':'8:1','node':'/dev/sda1','storage':'/tmp/mnt/11111111-2222-3333-4444-555555555555',
                'mounts':[{'target':'/opt','device':'8:1','root':'/','type':'ext4','readonly':False},
                          {'target':'/tmp/mnt/11111111-2222-3333-4444-555555555555','device':'8:1','root':'/','type':'ext4','readonly':False}]}
    def test_exact_two_ext4_mounts_only(self):
        self.assertEqual(validate_mount_plan(self.plan())['node'],'/dev/sda1')
    def test_extra_alias_wrong_device_or_readonly_refused(self):
        import copy
        for key,value in (('device','8:2'),('root','/subtree'),('type','tmpfs'),('readonly',True)):
            plan=copy.deepcopy(self.plan());plan['mounts'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_mount_plan(plan)
        plan=self.plan();plan['mounts'].append(dict(plan['mounts'][0],target='/other'))
        with self.assertRaises(ValueError):validate_mount_plan(plan)
    def test_unexpected_paths_and_device_names_refused(self):
        for key,value in (('node','/dev/sda'),('storage','/etc'),('device','8:1 extra')):
            with self.subTest(key=key),self.assertRaises(ValueError):validate_mount_plan({**self.plan(),key:value})
    def test_only_expected_entware_key_only_listener_can_be_stopped(self):
        command=[b'/opt/sbin/dropbear',b'-s',b'-p',b'192.168.1.1:222',b'-P',b'/opt/var/run/dropbear.pid']
        self.assertTrue(validate_listener('/opt/sbin/dropbear',b'\0'.join(command)+b'\0'))
        for argv in (command[1:],command+[b'-R'],[b'/opt/sbin/dropbear',b'-p',b'22']):
            with self.subTest(argv=argv),self.assertRaises(ValueError):
                validate_listener('/opt/sbin/dropbear',b'\0'.join(argv)+b'\0')
        with self.assertRaises(ValueError):validate_listener('/usr/sbin/dropbear',b'\0'.join(command)+b'\0')
    def test_overmounted_target_is_rejected_even_when_usb_rows_remain(self):
        plan=self.plan();storage=plan['storage']
        rows=f'1 0 8:1 / /opt rw - ext4 /dev/sda1 rw\n2 0 8:1 / {storage} rw - ext4 /dev/sda1 rw\n'
        self.assertTrue(verify_target_resolution(plan,rows,lambda _: '8:1'))
        with self.assertRaisesRegex(ValueError,'STORAGE_STACKED_MOUNT'):
            verify_target_resolution(plan,rows+'3 0 0:42 / /opt rw - tmpfs tmpfs rw\n',lambda _: '8:1')
        with self.assertRaisesRegex(ValueError,'STORAGE_TARGET_CHANGED'):
            verify_target_resolution(plan,rows,lambda target:'0:42' if target=='/opt' else '8:1')


if __name__=='__main__':unittest.main()
