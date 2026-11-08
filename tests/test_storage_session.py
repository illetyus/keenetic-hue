import unittest
from unittest.mock import Mock, patch
from hue_admin.storage_session import prepare


class PreparationTests(unittest.TestCase):
    def setUp(self):
        flags = patch.multiple('hue_admin.storage_session.os', O_NOFOLLOW=0x20000,
                               O_NONBLOCK=0x800, O_ACCMODE=3, create=True)
        flags.start(); self.addCleanup(flags.stop)

    def test_prepare_binds_current_plan_and_releases_readonly_handle_without_mutation(self):
        snapshot = {'mountPlan': {'node': '/dev/sda1', 'storage': '/tmp/mnt/fixture'}, 'sshListener': {'pid': 123}}
        runtime, commands, io = Mock(), Mock(), Mock()
        with patch('hue_admin.storage_session.readonly_preflight', return_value=snapshot), \
             patch('hue_admin.storage_session.os.open', return_value=77) as opening, \
             patch('hue_admin.storage_session.os.close') as closing, \
             patch('hue_admin.storage_session.identity_from_fd', return_value={'uuid': 'fixture'}), \
             patch('hue_admin.storage_session.LiveIO', return_value=io) as constructor:
            with prepare(runtime, lambda node, storage: commands) as result:
                self.assertIs(result, io)
                closing.assert_not_called()
                io.validate.assert_called_once(); io.independent.assert_called_once()
                commands.verify.assert_called_once()
                io.stop.assert_not_called(); io.mount.assert_not_called(); io.check.assert_not_called()
            closing.assert_called_once_with(77)
            constructor.assert_called_once_with(snapshot['mountPlan'], snapshot['sshListener'], 77,
                                                {'uuid': 'fixture'}, runtime, commands)
            import os
            self.assertEqual(opening.call_args.args[1] & os.O_ACCMODE, os.O_RDONLY)
            self.assertTrue(opening.call_args.args[1] & os.O_NOFOLLOW)

    def test_failed_independence_closes_handle_without_yield_or_service_stop(self):
        io = Mock(); io.independent.side_effect = RuntimeError('USB_DEPENDENCY')
        with patch('hue_admin.storage_session.readonly_preflight', return_value={'mountPlan': {'node': '/dev/sda1','storage':'/tmp/mnt/fixture'}, 'sshListener': {}}), \
             patch('hue_admin.storage_session.os.open', return_value=77), \
             patch('hue_admin.storage_session.os.close') as closing, \
             patch('hue_admin.storage_session.identity_from_fd', return_value={}), \
             patch('hue_admin.storage_session.LiveIO', return_value=io):
            with self.assertRaisesRegex(RuntimeError, 'USB_DEPENDENCY'):
                with prepare(Mock(), lambda *args: Mock()): self.fail('unsafe yield')
            closing.assert_called_once_with(77); io.stop.assert_not_called()


if __name__ == '__main__': unittest.main()
