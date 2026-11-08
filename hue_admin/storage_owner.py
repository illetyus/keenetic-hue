"""Compose detached maintenance and conservative recovery; internal trusted IO.

Journal and IO are reviewed fixed callbacks. RAM journal lock serializes worker
and recovery. The caller must hold an exclusive operator maintenance window;
this is not a public command and does not provision a RAM runtime itself.
"""
import copy
from .acceptance_launcher import detach
from .storage_maintenance import maintain

PHASES={'prepared','stopping','detaching','checking','restoring','closed','aborted_restored',
        'unclean_readonly','checker_stop_unknown','device_changed','restore_failed','publication_failed'}


def validate_state(value):
    if (not isinstance(value,dict) or set(value)!={'schema','phase','restoreVerified','clean'}
            or type(value['schema']) is not int or value['schema']!=1
            or type(value['phase']) is not str or value['phase'] not in PHASES
            or type(value['restoreVerified']) is not bool or type(value['clean']) is not bool
            or value['phase']=='closed' and (not value['restoreVerified'] or not value['clean'])
            or value['phase']=='aborted_restored' and not value['restoreVerified']):
        raise ValueError('STORAGE_OWNER_RECORD')
    return copy.deepcopy(value)


def recover_storage(io,journal):
    with journal.exclusive():
        state=validate_state(journal.load())
        if state['phase'] in ('closed','aborted_restored','unclean_readonly'):return state
        if state['phase'] in ('checker_stop_unknown','device_changed'):
            raise RuntimeError('STORAGE_OWNER_UNSAFE')
        if io.same_device() is not True:raise RuntimeError('STORAGE_DEVICE_CHANGED')
        if io.recovery_ready() is not True:raise RuntimeError('STORAGE_RECOVERY_HOLDERS')
        # A checker interrupted before recording clean is never presumed clean.
        readonly=not state['clean'] and state['phase'] in ('checking','restoring','restore_failed','publication_failed')
        state.update(phase='restoring',restoreVerified=False)
        record_error=None
        try:journal.save(state)
        except Exception as error:record_error=error
        for name in ('storage','opt'):
            if io.mounted(name) is not True:io.mount(name,readonly)
            if io.mounted(name) is not True or io.readonly(name) is not readonly:
                raise RuntimeError('STORAGE_RECOVERY_MOUNT_UNVERIFIED')
        if readonly:
            state['phase']='unclean_readonly'
        else:
            if io.start() is not True or io.healthy() is not True:
                raise RuntimeError('STORAGE_RECOVERY_SERVICES_UNVERIFIED')
            state.update(phase='aborted_restored',restoreVerified=True)
        if record_error is not None:raise record_error
        journal.save(state)
        # No evidence minted during recovery. Even a clean journal lacks the
        # original measured output needed to produce a new acceptance proof.
        return state


def launch_maintenance(io,journal,*,timeout=900,before_run=None,on_exit=None,preserve_fds=()):
    def run():
        if before_run is not None:before_run()
        if hasattr(io,'prepare_process'):io.prepare_process()
        with journal.exclusive():
            try:previous=validate_state(journal.load())
            except FileNotFoundError:previous=None
            if previous is not None and previous['phase'] not in ('closed','aborted_restored'):
                raise RuntimeError('STORAGE_RECOVERY_REQUIRED')
            value=maintain(io,journal.save)
            return value['phase']=='closed' and value['restoreVerified'] is True
    def recover():
        if hasattr(io,'prepare_process'):io.prepare_process()
        value=recover_storage(io,journal)
        return value['phase'] in ('closed','aborted_restored') and value['restoreVerified'] is True
    descriptors=((io.fd,) if hasattr(io,'fd') else ())+preserve_fds
    return detach(run,recover,timeout=timeout,recovery_timeout=180,preserve_fds=descriptors,on_exit=on_exit)
