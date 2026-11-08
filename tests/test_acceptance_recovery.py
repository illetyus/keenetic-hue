import copy
from contextlib import contextmanager
import unittest
import uuid

from hue_admin.acceptance_recovery import recover_interrupted, validate_snapshot


def snapshot():
    return {'schema': 1, 'operationId': str(uuid.uuid4()), 'phase': 'running',
            'restoreVerified': False, 'supervisorPid': 100, 'workerPid': 101,
            'outcome': None, 'baselineRelease': '0.2.0-rc.5',
            'candidateRelease': '0.2.0-rc.7',
            'baselineFingerprints': {'config/agent.json': 'a' * 64},
            'baselinePhysical': {'on': False, 'brightnessRaw': 50}}


class Journal:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        self.records = []

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.records.append(copy.deepcopy(value))
        self.value = copy.deepcopy(value)


class Adapter:
    def __init__(self):
        self.release = '0.2.0-rc.7'
        self.actions = []
        self.locked = False

    @contextmanager
    def exclusive(self):
        self.locked = True
        try:
            yield
        finally:
            self.locked = False

    def selected(self):
        return self.release

    def recover(self):
        assert self.locked
        self.actions.append('recover')

    def rollback(self):
        self.actions.append('rollback')
        self.release = '0.2.0-rc.5'

    def start(self):
        self.actions.append('start')

    def healthy(self, release):
        return self.release == release

    def fingerprints(self):
        return {'config/agent.json': 'a' * 64}

    def physical(self):
        return {'on': False, 'brightnessRaw': 50}


class RecoveryTests(unittest.TestCase):
    def test_interrupted_candidate_is_restored_and_verified_under_lock(self):
        adapter = Adapter()
        journal = Journal(snapshot())
        result = recover_interrupted(adapter, journal, lambda _: True)
        self.assertEqual(adapter.actions, ['recover', 'rollback', 'start'])
        self.assertEqual([v['phase'] for v in journal.records], ['restoring', 'closed'])
        self.assertEqual(result['outcome'], 'interrupted')
        self.assertTrue(result['restoreVerified'])

    def test_live_or_ambiguous_owner_blocks_before_any_mutation(self):
        for quiet in (False, None):
            with self.subTest(quiet=quiet):
                adapter = Adapter()
                journal = Journal(snapshot())
                with self.assertRaisesRegex(RuntimeError, 'ACCEPTANCE_OWNER_ACTIVE'):
                    recover_interrupted(adapter, journal, lambda _: quiet)
                self.assertEqual(adapter.actions, [])
                self.assertEqual(journal.records, [])

    def test_unrelated_selected_release_is_never_recovered_or_rolled_back(self):
        adapter = Adapter()
        adapter.release = '0.3.0'
        journal = Journal(snapshot())
        with self.assertRaisesRegex(RuntimeError, 'ACCEPTANCE_SELECTION_CHANGED'):
            recover_interrupted(adapter, journal, lambda _: True)
        self.assertEqual(adapter.actions, [])

    def test_already_restored_baseline_does_not_rollback(self):
        adapter = Adapter()
        adapter.release = '0.2.0-rc.5'
        result = recover_interrupted(adapter, Journal(snapshot()), lambda _: True)
        self.assertNotIn('rollback', adapter.actions)
        self.assertTrue(result['restoreVerified'])

    def test_changed_master_or_lamp_cannot_be_marked_closed(self):
        for method, value in (('fingerprints', {'config/agent.json': 'b' * 64}),
                              ('physical', {'on': True, 'brightnessRaw': 50}),
                              ('healthy', False)):
            with self.subTest(method=method):
                adapter = Adapter()
                setattr(adapter, method, lambda *_, value=value: value)
                result = recover_interrupted(adapter, Journal(snapshot()), lambda _: True)
                self.assertEqual(result['phase'], 'recovery_failed')
                self.assertFalse(result['restoreVerified'])

    def test_prepare_record_failure_prevents_recovery(self):
        adapter = Adapter()
        journal = Journal(snapshot())
        journal.save = lambda _: (_ for _ in ()).throw(OSError('fixture'))
        with self.assertRaises(OSError):
            recover_interrupted(adapter, journal, lambda _: True)
        self.assertEqual(adapter.actions, [])

    def test_terminal_save_failure_is_propagated(self):
        adapter = Adapter()
        journal = Journal(snapshot())
        save = journal.save
        def broken(value):
            if value['phase'] == 'closed':
                raise OSError('fixture')
            save(value)
        journal.save = broken
        with self.assertRaises(OSError):
            recover_interrupted(adapter, journal, lambda _: True)
        self.assertEqual(journal.value['phase'], 'restoring')

    def test_closed_receipt_is_idempotent(self):
        value = snapshot()
        value.update(phase='closed', restoreVerified=True, outcome='completed')
        adapter = Adapter()
        self.assertEqual(recover_interrupted(adapter, Journal(value), lambda _: self.fail()), value)
        self.assertEqual(adapter.actions, [])

    def test_malformed_snapshot_is_rejected_before_callback(self):
        for changes in ({'operationId': '../x'}, {'baselineRelease': '../x'},
                        {'baselinePhysical': {'on': 1, 'brightnessRaw': 50}},
                        {'baselineFingerprints': {'config/agent.json': 'secret'}},
                        {'restoreVerified': True}, {'workerPid': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                recover_interrupted(Adapter(), Journal({**snapshot(), **changes}), lambda _: self.fail())


if __name__ == '__main__':
    unittest.main()
