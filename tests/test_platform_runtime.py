from types import SimpleNamespace
import unittest
from unittest.mock import patch
from hue_admin.platform import runtime_for


class PlatformRuntimeTests(unittest.TestCase):
    def test_root_platform_probe_uses_absolute_entware_program(self):
        replies=[SimpleNamespace(stdout='arch all 100\narch mips-3.4 150\n'),
                 SimpleNamespace(stdout='libusb-1.0 - 1.0.29-1\n')]
        with patch('hue_admin.platform.subprocess.run',side_effect=replies) as run, \
             patch('hue_admin.platform.version',return_value='0.0.236'):
            result=runtime_for({'bumble':'0.0.236','opkg.libusb-1.0':'1.0.29-1'})
        self.assertEqual(run.call_args_list[0].args[0],['/opt/bin/opkg','print-architecture'])
        self.assertEqual(run.call_args_list[1].args[0],['/opt/bin/opkg','list-installed'])
        self.assertEqual(result[2],{'bumble':'0.0.236','opkg.libusb-1.0':'1.0.29-1'})
