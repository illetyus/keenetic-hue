"""Recover an interrupted controlled acceptance under the same admission lock.

No CLI/startup hook yet. The trusted journal authenticates durable snapshots;
quiescent must reject live or ambiguous original owners before any mutation.
No lamp writes or PID-based termination are performed by this module.
"""
import copy
import re
import uuid

from .service_manager import valid_id


def validate_snapshot(value):
    required = {'schema', 'operationId', 'phase', 'restoreVerified',
                'supervisorPid', 'outcome', 'baselineRelease', 'candidateRelease',
                'baselineFingerprints', 'baselinePhysical'}
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - {'workerPid', 'reason'}:
        raise ValueError('ACCEPTANCE_RECEIPT')
    try:
        if type(value['operationId']) is not str or str(uuid.UUID(value['operationId'])) != value['operationId']:
            raise ValueError('ACCEPTANCE_RECEIPT')
    except (ValueError, AttributeError):
        raise ValueError('ACCEPTANCE_RECEIPT') from None
    if (type(value['schema']) is not int or value['schema'] != 1
            or any(not valid_id(value[k]) for k in ('baselineRelease', 'candidateRelease'))
            or value['baselineRelease'] == value['candidateRelease']
            or type(value['phase']) is not str
            or value['phase'] not in ('prepared', 'running', 'restoring', 'closed', 'recovery_failed', 'owner_not_stopped')
            or type(value['restoreVerified']) is not bool
            or value['phase'] == 'closed' and not value['restoreVerified']
            or value['restoreVerified'] and value['phase'] not in ('closed', 'recovery_failed')
            or value['outcome'] not in (None, 'completed', 'failed', 'timed_out', 'interrupted')
            or any(type(value[k]) is not int or not 1 <= value[k] <= 2147483647
                   for k in ('supervisorPid', 'workerPid') if k in value)
            or 'reason' in value and value['reason'] not in ('RECEIPT_OR_WORKER_SETUP_FAILED', 'INTERRUPTED_RECOVERY')):
        raise ValueError('ACCEPTANCE_RECEIPT')
    hashes = value['baselineFingerprints']
    physical = value['baselinePhysical']
    if (not isinstance(hashes, dict) or not 1 <= len(hashes) <= 32
            or any(type(k) is not str or not 1 <= len(k) <= 128
                   or type(v) is not str or not re.fullmatch('[a-f0-9]{64}', v) for k, v in hashes.items())
            or not isinstance(physical, dict) or set(physical) != {'on', 'brightnessRaw'}
            or type(physical['on']) is not bool or type(physical['brightnessRaw']) is not int
            or not 0 <= physical['brightnessRaw'] <= 254):
        raise ValueError('ACCEPTANCE_RECEIPT')
    return copy.deepcopy(value)


def recover_interrupted(adapter, journal, quiescent):
    """Caller supplies fixed trusted adapters; no acceptance worker is resumed."""
    with adapter.exclusive():
        state = validate_snapshot(journal.load())
        if state['phase'] == 'closed':
            return state
        if quiescent(state) is not True:
            raise RuntimeError('ACCEPTANCE_OWNER_ACTIVE')
        baseline, candidate = state['baselineRelease'], state['candidateRelease']
        if adapter.selected() not in (baseline, candidate, None):
            raise RuntimeError('ACCEPTANCE_SELECTION_CHANGED')
        state.update(phase='restoring', restoreVerified=False, outcome='interrupted', reason='INTERRUPTED_RECOVERY')
        journal.save(state)  # Failed durable intention must prevent recovery mutation.
        try:
            adapter.recover()
            selected = adapter.selected()
            if selected == candidate:
                adapter.rollback()
            elif selected != baseline:
                raise RuntimeError('ACCEPTANCE_SELECTION_CHANGED')
            adapter.start()
            state['restoreVerified'] = (adapter.selected() == baseline and adapter.healthy(baseline) is True
                                        and adapter.fingerprints() == state['baselineFingerprints']
                                        and adapter.physical() == state['baselinePhysical'])
        except Exception:
            state['restoreVerified'] = False
        state['phase'] = 'closed' if state['restoreVerified'] else 'recovery_failed'
        journal.save(state)  # Never acknowledge a terminal write failure.
        return state
