"""Verify selected signed policy; caller must hold the management lock.

This check grants no capability and performs no kernel operation. A production
caller must retain the lock throughout the authorized operation and use a fixed
sysctl target; no CLI or boot integration is provided here.
"""
from pathlib import Path


def authorized_release(root):
    from .service_manager import selection, verify_selected
    from .runtime_policy import release_policy

    root = Path(root)
    release = selection(root)
    verify_selected(root, release)
    policy = release_policy(root, release)
    if policy.get('kernel') is not True:
        raise ValueError('KERNEL_POLICY_DISABLED')
    if selection(root) != release:
        raise ValueError('KERNEL_SELECTION_CHANGED')
    return release
