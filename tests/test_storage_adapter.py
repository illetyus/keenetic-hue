import unittest
import json
from pathlib import Path
from unittest.mock import patch,Mock
from hue_admin.storage_adapter import LiveIO


class AdapterTests(unittest.TestCase):
    def make(self):
        plan={'device':'8:1','node':'/dev/sda1','storage':'/tmp/mnt/fixture',
              'mounts':[{'target':p,'device':'8:1','root':'/','type':'ext4','readonly':False}
                        for p in ('/opt','/tmp/mnt/fixture')]}
        return LiveIO(plan,{'pid':10,'startTicks':20},3,{},Mock(),Mock())
    def test_stop_rechecks_listener_identity_before_any_service_command(self):
        io=self.make()
        with patch('hue_admin.storage_adapter.listener_identity',return_value={'pid':11,'startTicks':20}), \
             patch.object(io,'_service') as command:
            with self.assertRaisesRegex(ValueError,'STORAGE_SSH_OWNER_CHANGED'):io.stop()
            command.assert_not_called()
    def test_stop_uses_normal_hue_then_ssh_commands(self):
        io=self.make()
        with patch('hue_admin.storage_adapter.listener_identity',return_value=io.listener), \
             patch.object(io,'_service',return_value=True) as command:
            self.assertTrue(io.stop())
            self.assertEqual([c.args for c in command.call_args_list],[('hue','stop'),('ssh','stop')])
    def test_mounts_are_fixed_order_no_force_and_no_journal_replay_on_unclean(self):
        io=self.make()
        with patch.object(io,'same_device',return_value=True),patch.object(io,'mounted',return_value=True),patch.object(io,'_ram') as run:
            io.unmount('opt');io.mount('storage',True);io.mount('opt',True)
            self.assertEqual(run.call_args_list[0].args,('umount','/opt'))
            self.assertEqual(run.call_args_list[1].args,('mount','-t','ext4','-o','ro,noload,noatime','/dev/sda1','/tmp/mnt/fixture'))
            self.assertEqual(run.call_args_list[2].args,('mount','-o','bind','/tmp/mnt/fixture','/opt'))
            self.assertEqual(run.call_args_list[3].args,('mount','-o','remount,bind,ro','/opt'))
            with self.assertRaises(ValueError):io.unmount('untrusted')
    def test_changed_device_blocks_mount_and_check(self):
        io=self.make()
        with patch.object(io,'same_device',return_value=False),patch.object(io,'_ram') as run:
            with self.assertRaises(ValueError):io.mount('storage',False)
            with self.assertRaises(ValueError):io.check()
            run.assert_not_called()
    def test_start_ssh_first_then_hue_and_no_start_after_ssh_failure(self):
        io=self.make()
        with patch.object(io,'_service',return_value=True) as run:
            self.assertTrue(io.start());self.assertEqual([c.args for c in run.call_args_list],[('ssh','start'),('hue','start')])
        with patch.object(io,'_service',return_value=False) as run:
            self.assertFalse(io.start());run.assert_called_once_with('ssh','start')

    def test_healthy_uses_fixed_entware_live_health_command(self):
        io=self.make()
        value={'schemaVersion':1,'healthy':True,'releaseId':'0.2.0-rc.5','components':{
            'agent':{'state':'running','reason':'OWNED_VERSION'},
            'tunnel':{'state':'ready','reason':'TUNNEL_READY'},
            'manager':{'state':'running','reason':'BOTH_COMPONENTS_OWNED'},
            'localApi':{'state':'ready','reason':'API_RESPONSE'},
            'ble':{'state':'ready','reason':'VERIFIED'}}}
        output=json.dumps(value,separators=(',',':'))
        result={'exitCode':0,'outputSha256':'0'*64,'outputBytes':len(output),'outputTail':output}
        with patch('hue_admin.storage_adapter.listener_identity',return_value={'pid':11,'startTicks':20}), \
             patch('hue_admin.unified_health.status',return_value=value), \
             patch('hue_admin.storage_adapter.run_owned',return_value=result) as run:
            self.assertTrue(io.healthy())
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0],['/opt/bin/huectl','health','--live','--json'])
        self.assertEqual(run.call_args.kwargs['cwd'],'/')


if __name__=='__main__':unittest.main()
