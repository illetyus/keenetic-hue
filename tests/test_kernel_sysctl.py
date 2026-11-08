import os
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'), 'POSIX sysctl boundary')
class SysctlBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'fs').mkdir()
        (self.root / 'kernel').mkdir()
        (self.root / 'fs/protected_hardlinks').write_text('0\n')
        (self.root / 'kernel/randomize_va_space').write_text('2\n')

    def test_read_write_only_known_controls_and_preserve_aslr(self):
        from hue_admin.kernel_sysctl import SysctlFiles
        with SysctlFiles(self.root) as controls:
            self.assertEqual(controls.read('fs.protected_hardlinks'), 0)
            controls.write('fs.protected_hardlinks', 1)
            self.assertEqual(controls.read('fs.protected_hardlinks'), 1)
            self.assertEqual(controls.read('kernel.randomize_va_space'), 2)
            with self.assertRaises(ValueError):
                controls.write('kernel.randomize_va_space', 1)
            with self.assertRaises(ValueError):
                controls.read('../outside')

    def test_missing_optional_control_is_not_assumed_enabled(self):
        from hue_admin.kernel_sysctl import SysctlFiles
        with SysctlFiles(self.root) as controls:
            self.assertIsNone(controls.read('fs.protected_symlinks'))
            (self.root / 'kernel/randomize_va_space').unlink()
            with self.assertRaises(FileNotFoundError):
                controls.read('kernel.randomize_va_space')

    def test_symlink_control_and_directory_are_rejected(self):
        from hue_admin.kernel_sysctl import SysctlFiles
        path = self.root / 'fs/protected_hardlinks'
        path.unlink()
        path.symlink_to(self.root / 'kernel/randomize_va_space')
        with SysctlFiles(self.root) as controls, self.assertRaises(OSError):
            controls.read('fs.protected_hardlinks')
        (self.root / 'kernel/randomize_va_space').unlink()
        (self.root / 'kernel').rmdir()
        (self.root / 'kernel').symlink_to(self.root / 'fs', target_is_directory=True)
        with self.assertRaises(OSError):
            with SysctlFiles(self.root):
                pass

    def test_fifo_is_rejected_without_waiting_for_another_process(self):
        from hue_admin.kernel_sysctl import SysctlFiles
        path = self.root / 'fs/protected_hardlinks'
        path.unlink()
        os.mkfifo(path)
        with SysctlFiles(self.root) as controls, self.assertRaises(ValueError):
            controls.read('fs.protected_hardlinks')
