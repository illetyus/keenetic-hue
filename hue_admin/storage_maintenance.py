"""Injected maintenance transaction; no real-device adapter or command entry.

Trusted IO must provide bounded, identity-pinned operations and a RAM-only
durable record/recovery owner. Caller holds exclusive maintenance admission.
Never use lazy/force unmount. An unknown checker stop forbids any remount.
"""
import copy


def maintain(io,record):
    io.validate()
    if io.independent() is not True:raise RuntimeError('RAM_RUNTIME_DEPENDENCY')
    if io.same_device() is not True:raise RuntimeError('STORAGE_DEVICE_CHANGED')
    if any(io.mounted(name) is not True or io.readonly(name) is not False for name in ('opt','storage')):
        raise RuntimeError('STORAGE_INITIAL_MOUNTS')
    state={'schema':1,'phase':'prepared','restoreVerified':False,'clean':False}
    def save(phase):state['phase']=phase;record(copy.deepcopy(state))
    save('prepared')
    stopped=False;check_started=False;unknown=None;result=None
    try:
        save('stopping');stopped=True
        if io.stop() is not True:raise RuntimeError('STORAGE_STOP_FAILED')
        if io.quiescent() is not True:raise RuntimeError('STORAGE_HOLDERS_REMAIN')
        if io.independent() is not True:raise RuntimeError('RAM_RUNTIME_DEPENDENCY')
        save('detaching')
        for name in ('opt','storage'):
            if io.same_device() is not True:raise RuntimeError('STORAGE_DEVICE_CHANGED')
            io.unmount(name)
            if io.mounted(name) is not False:raise RuntimeError('STORAGE_UNMOUNT_FAILED')
        if io.same_device() is not True:raise RuntimeError('STORAGE_DEVICE_CHANGED')
        save('checking');check_started=True
        try:result=io.check()
        except BaseException as error:
            unknown=error if str(error)=='FSCK_STOP_UNCONFIRMED' else None
            raise
        state['clean']=True
    except BaseException as error:
        if str(error)=='COMMAND_STOP_UNCONFIRMED':unknown=error
        raise
    finally:
        if stopped:
            if unknown is not None:
                try:save('checker_stop_unknown')
                except BaseException as error:unknown.recordError=error
                # Preserve checker ownership/claim; caller must not remount.
            else:
                if io.same_device() is not True:
                    save('device_changed');raise RuntimeError('STORAGE_DEVICE_CHANGED')
                readonly=check_started and not state['clean']
                record_error=None
                try:save('restoring')
                except Exception as error:record_error=error
                for name in ('storage','opt'):
                    if io.mounted(name) is not True:io.mount(name,readonly)
                    if io.mounted(name) is not True or io.readonly(name) is not readonly:
                        save('restore_failed');raise RuntimeError('STORAGE_REMOUNT_UNVERIFIED')
                if readonly:
                    if record_error is not None:raise record_error
                    save('unclean_readonly')
                else:
                    if state['clean'] and io.publish(result) is not True:
                        save('publication_failed');raise RuntimeError('STORAGE_PUBLICATION_FAILED')
                    if io.start() is not True or io.healthy() is not True:
                        save('restore_failed');raise RuntimeError('STORAGE_SERVICES_UNVERIFIED')
                    state['restoreVerified']=True
                    if record_error is not None:raise record_error
                    save('closed' if state['clean'] else 'aborted_restored')
    return copy.deepcopy(state)
