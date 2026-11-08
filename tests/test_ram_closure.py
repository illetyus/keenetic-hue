import os
import unittest
from hue_admin.ram_closure import mappings_for_device, assert_independent


class ClosureTests(unittest.TestCase):
    def test_mapping_uses_device_number_even_under_other_mount_alias(self):
        rows='100-200 r-xp 0000 08:01 123 /tmp/mnt/usb/python\n200-300 rw-p 0000 00:00 0\n'
        self.assertEqual(mappings_for_device(rows,8,1),1)
        self.assertEqual(mappings_for_device(rows,8,2),0)

    @unittest.skipUnless(os.name=='posix','Native proc closure')
    def test_own_current_filesystem_cannot_be_reported_independent(self):
        with self.assertRaisesRegex(RuntimeError,'RAM_RUNTIME_DEPENDENCY'):
            assert_independent(os.stat(__file__).st_dev)


if __name__=='__main__':unittest.main()
