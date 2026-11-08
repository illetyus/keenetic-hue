"""Kernel transaction logic with injected IO; never opens procfs itself.

The caller must serialize all operations, authenticate receipts and make every
save durable before returning. No boot persistence or live adapter is enabled
by this module. Recovery must precede a fresh transaction after interruption.
"""

from copy import deepcopy

from .kernel_policy import ASLR, CONTROLS, plan_kernel

_PHASES = {'prepared', 'applying', 'applied', 'restoring', 'restored',
           'rollback_conflict'}


def _validated(receipt):
    if not isinstance(receipt, dict) or set(receipt) != {
        'schema', 'observed', 'attempted', 'phase'
    }:
        raise ValueError('KERNEL_RECEIPT')
    if type(receipt['schema']) is not int or receipt['schema'] != 1:
        raise ValueError('KERNEL_RECEIPT')
    if type(receipt['phase']) is not str or receipt['phase'] not in _PHASES:
        raise ValueError('KERNEL_RECEIPT')
    plan = plan_kernel(receipt['observed'])
    attempted = receipt['attempted']
    names = list(plan['changes'])
    if (type(attempted) is not list or
            any(type(name) is not str for name in attempted) or
            attempted != names[:len(attempted)]):
        raise ValueError('KERNEL_RECEIPT')
    if receipt['phase'] == 'prepared' and attempted:
        raise ValueError('KERNEL_RECEIPT')
    if receipt['phase'] == 'applied' and attempted != names:
        raise ValueError('KERNEL_RECEIPT')
    return deepcopy(receipt), plan


def _save(save, receipt):
    # Callers cannot retain a reference and silently mutate durable history.
    save(deepcopy(receipt))


def _equal(value, expected):
    return type(value) is type(expected) and value == expected


def restore_kernel(receipt, read, write, save):
    """Restore only attempted zero-to-one changes; retain external conflicts.

    Continue other keys after a read/write failure, then retain a retryable
    receipt. An interrupted restore is safe to repeat with the last receipt.
    """
    state, plan = _validated(receipt)
    if state['phase'] == 'restored':
        return state
    state['phase'] = 'restoring'
    record_failed = False
    try:
        _save(save, state)
    except Exception:
        # The prior intention receipt already supports interrupted recovery.
        # Storage loss must not prevent best-effort undo of journaled writes.
        record_failed = True
    conflict = False
    for name in reversed(state['attempted']):
        change = plan['changes'][name]
        try:
            value = read(name)
            if _equal(value, change['before']):
                continue
            if not _equal(value, change['after']):
                conflict = True
                continue
            write(name, change['before'])
            if not _equal(read(name), change['before']):
                conflict = True
        except Exception:
            conflict = True
    state['phase'] = 'rollback_conflict' if conflict else 'restored'
    try:
        _save(save, state)
    except Exception:
        raise ValueError('KERNEL_ROLLBACK_RECORD') from None
    if conflict:
        raise ValueError('KERNEL_ROLLBACK_CONFLICT')
    if record_failed:
        raise ValueError('KERNEL_ROLLBACK_RECORD')
    return state


def apply_kernel(read, write, save, accept):
    """Record baseline, apply allowlisted changes, verify, or roll back.

    Process death deliberately propagates without in-process cleanup: the last
    durable attempted list is sufficient for restore_kernel on recovery.
    """
    observed = {name: read(name) for name in (*CONTROLS, ASLR)}
    plan = plan_kernel(observed)
    state = {'schema': 1, 'observed': observed, 'attempted': [],
             'phase': 'prepared'}
    _save(save, state)
    try:
        for name, change in plan['changes'].items():
            if not _equal(read(name), change['before']):
                raise ValueError('KERNEL_CONCURRENT_CHANGE')
            candidate = deepcopy(state)
            candidate['attempted'].append(name)
            candidate['phase'] = 'applying'
            _save(save, candidate)
            if not _equal(read(name), change['before']):
                raise ValueError('KERNEL_CONCURRENT_CHANGE')
            state = candidate
            write(name, change['after'])
            if not _equal(read(name), change['after']):
                raise ValueError('KERNEL_READBACK')
        if accept() is not True:
            raise ValueError('KERNEL_ACCEPTANCE')
        for name, before in observed.items():
            expected = plan['changes'].get(name, {}).get('after', before)
            if not _equal(read(name), expected):
                raise ValueError('KERNEL_READBACK')
        state['phase'] = 'applied'
        _save(save, state)
        return deepcopy(state)
    except Exception:
        restore_kernel(state, read, write, save)
        raise
