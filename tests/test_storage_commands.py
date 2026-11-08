import unittest
from unittest.mock import patch
from hue_admin.storage_commands import RamCommands


class CommandTests(unittest.TestCase):
    def make(self):
        return RamCommands('/tmp/private/busybox', '/tmp/private/lib',
                           {'files': {}, 'links': {}}, '/dev/sda1', '/tmp/mnt/fixture')

    def test_only_exact_transaction_commands_reach_owned_runner(self):
        command = self.make()
        allowed = [
            ('umount', '/opt'), ('umount', '/tmp/mnt/fixture'),
            ('mount', '-t', 'ext4', '-o', 'rw,noatime,data=ordered', '/dev/sda1', '/tmp/mnt/fixture'),
            ('mount', '-t', 'ext4', '-o', 'ro,noload,noatime', '/dev/sda1', '/tmp/mnt/fixture'),
            ('mount', '-o', 'bind', '/tmp/mnt/fixture', '/opt'),
            ('mount', '-o', 'remount,bind,ro', '/opt'),
        ]
        with patch.object(command, 'verify'), patch('hue_admin.storage_commands.run_owned', return_value={'exitCode': 0}) as run:
            for args in allowed:
                self.assertTrue(command(args))
            self.assertEqual(run.call_count, len(allowed))
            self.assertEqual(run.call_args.kwargs['timeout'], 30)
            self.assertEqual(run.call_args.args[0][-4:], list(allowed[-1]))

    def test_arbitrary_force_lazy_repair_and_other_devices_refused_before_verification(self):
        command = self.make()
        with patch.object(command, 'verify') as verify, patch('hue_admin.storage_commands.run_owned') as run:
            for args in [('umount', '-l', '/opt'), ('umount', '-f', '/opt'),
                         ('mkfs.ext4', '/dev/sda1'), ('mount', '/dev/sdb1', '/opt'),
                         ('sh', '-c', 'mount'), ['umount', '/opt']]:
                with self.assertRaisesRegex(ValueError, 'STORAGE_COMMAND_ARGUMENTS'):
                    command(args)
            verify.assert_not_called(); run.assert_not_called()

    def test_failed_verification_cannot_execute_and_nonzero_is_failure(self):
        command = self.make()
        with patch.object(command, 'verify', side_effect=ValueError('RAM_HASH')), patch('hue_admin.storage_commands.run_owned') as run:
            with self.assertRaisesRegex(ValueError, 'RAM_HASH'): command(('umount', '/opt'))
            run.assert_not_called()
        with patch.object(command, 'verify'), patch('hue_admin.storage_commands.run_owned', return_value={'exitCode': 1}):
            self.assertFalse(command(('umount', '/opt')))


if __name__ == '__main__': unittest.main()
