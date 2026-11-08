"""Prepare trusted RAM maintenance IO without stopping a service or mounting.

Only the internal RAM entry may use this context. The command factory is a
trusted provisioner binding RamCommands to the freshly observed device/target.
The yielded descriptor must remain owned until detached handoff has completed.
"""
from contextlib import contextmanager
import os

from .storage_live import readonly_preflight
from .storage_acceptance import identity_from_fd
from .storage_adapter import LiveIO


@contextmanager
def prepare(runtime, command_factory):
    snapshot = readonly_preflight()
    plan = snapshot['mountPlan']
    commands = command_factory(plan['node'], plan['storage'])
    commands.verify()
    fd = os.open(plan['node'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        identity = identity_from_fd(fd)
        io = LiveIO(plan, snapshot['sshListener'], fd, identity, runtime, commands)
        io.validate()
        io.independent()
        yield io
    finally:
        os.close(fd)
