"""Unwired internal kernel apply boundary; requires reviewed acceptance.

Not exported through any command or startup hook. The acceptance callback must
be supplied by trusted code, and real-world safety/persistence is unverified.
"""
from pathlib import Path

from .kernel_authorization import authorized_release
from .kernel_controller import apply_policy, reconcile_policy, ensure_policy, restore_policy
from .restore_current import management_lock
from .service_manager import SYSTEM, selection, verify_selected


def apply_managed(accept):
    with management_lock(SYSTEM):
        authorized_release(SYSTEM)
        return apply_policy(SYSTEM, Path('/proc/sys'), accept)


def ensure_managed(accept):
    """Verify authorization and ensure policy under the management lock.

    An already-applied matching same-boot receipt does not rerun acceptance.
    Acceptance must not depend on release identity unless receipt provenance
    is extended to bind it to the selected release.
    """
    with management_lock(SYSTEM):
        authorized_release(SYSTEM)
        return ensure_policy(SYSTEM, Path('/proc/sys'), accept)


def reconcile_managed():
    """Retire a matching old-boot receipt; never write a sysctl value."""
    with management_lock(SYSTEM):
        release = selection(SYSTEM)
        verify_selected(SYSTEM, release)
        if selection(SYSTEM) != release:
            raise ValueError('KERNEL_SELECTION_CHANGED')
        return reconcile_policy(SYSTEM, Path('/proc/sys'))


def restore_managed():
    """Restore a same-boot receipt under the verified management boundary."""
    with management_lock(SYSTEM):
        release = selection(SYSTEM)
        verify_selected(SYSTEM, release)
        if selection(SYSTEM) != release:
            raise ValueError('KERNEL_SELECTION_CHANGED')
        return restore_policy(SYSTEM, Path('/proc/sys'))
