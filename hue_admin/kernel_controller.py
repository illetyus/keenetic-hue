"""Compose the kernel boundary for fixture use; not wired to CLI or boot.

Callers must hold a process-wide management lock and supply trusted roots.
Production use additionally requires signed policy authorization, an explicit
acceptance procedure, persistence semantics and a reviewed recovery owner.
"""
from .kernel_receipt import KernelReceipt
from .kernel_sysctl import SysctlFiles
from .kernel_transaction import apply_kernel, restore_kernel
from .kernel_policy import plan_kernel


def apply_policy(root, sysctl_root, accept):
    receipt = KernelReceipt(root)
    try:
        previous = receipt.load()
    except FileNotFoundError:
        previous = None
    if previous is not None and previous['phase'] != 'restored':
        raise ValueError('KERNEL_RECOVERY_REQUIRED')
    with SysctlFiles(sysctl_root) as controls:
        return apply_kernel(controls.read, controls.write, receipt.save, accept)


def restore_policy(root, sysctl_root):
    receipt = KernelReceipt(root)
    value = receipt.load()
    with SysctlFiles(sysctl_root) as controls:
        return restore_kernel(value, controls.read, controls.write, receipt.save)


def reconcile_policy(root, sysctl_root):
    receipt = KernelReceipt(root)
    with SysctlFiles(sysctl_root) as controls:
        return receipt.reconcile_boot(controls.read)


def ensure_policy(root, sysctl_root, accept):
    """Fixture lifecycle; caller holds lock and supplies trusted roots.

    Existing applied state is checked, not written; its original acceptance
    is not rerun. An incomplete same-boot transaction is restored before
    retry. Same-boot conflicts block; prior-boot records require baseline
    reconciliation before a fresh transaction.
    """
    receipt = KernelReceipt(root)
    with SysctlFiles(sysctl_root) as controls:
        try:
            value = receipt.load()
        except FileNotFoundError:
            value = None
        except ValueError as error:
            if str(error) != 'KERNEL_RECEIPT_BOOT_CHANGED':
                raise
            value = receipt.reconcile_boot(controls.read)
        if value is not None and value['phase'] == 'applied':
            changes = plan_kernel(value['observed'])['changes']
            for name, before in value['observed'].items():
                expected = changes.get(name, {}).get('after', before)
                actual = controls.read(name)
                if type(actual) is not type(expected) or actual != expected:
                    raise ValueError('KERNEL_APPLIED_STATE_CHANGED')
            return value
        if value is not None and value['phase'] == 'rollback_conflict':
            raise ValueError('KERNEL_RECOVERY_REQUIRED')
        if value is not None and value['phase'] != 'restored':
            restore_kernel(value, controls.read, controls.write, receipt.save)
        return apply_kernel(controls.read, controls.write, receipt.save, accept)
