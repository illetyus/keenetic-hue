"""Fixed boot recovery adapter. Caller must already hold CLI admission.

Only the new private journal is recognized. Old RC6 test-window records are
never rewritten or treated as successful. This module cannot launch a test.
"""
from contextlib import nullcontext
from pathlib import Path
import time

from . import service_manager as manager
from .acceptance_journal import AcceptanceJournal, NAME
from .acceptance_recovery import recover_interrupted
from .controlled_acceptance import ManagedRelease
from .health import authenticated_probe


def physical_snapshot(root):
    """Read only from the owned authenticated agent; never issues lamp writes."""
    from .runtime_policy import release_policy
    root=Path(root)
    selected=manager.selection(root)
    record=manager.owner_record('agent',root)
    pid=manager.child_pid('agent',root)
    if (not record or record['releaseId']!=selected or not pid
            or not manager.supervisor_owned('agent',root,record)
            or not manager.child_owned('agent',root,record,pid)):
        raise ValueError('ACCEPTANCE_AGENT_OWNER')
    socket_path=root/'run/health.sock'
    identity_options={}
    if release_policy(root,selected)['agent']=='dedicated':
        from .privileges import load_service_identity
        from .agent_storage import agent_runtime_paths
        identity=load_service_identity(root,'hue-agent')
        socket_path=agent_runtime_paths(root,selected)['health_socket']
        identity_options={'expected_uid':identity['uid'],'expected_gid':identity['gid']}
    def token_loader():
        token=manager.private_bytes(root/'secrets/api.token',512).decode('ascii').strip()
        if not 32<=len(token)<=256 or any(c.isspace() for c in token):
            raise ValueError('ACCEPTANCE_API_TOKEN')
        return token
    code,value=authenticated_probe(8787,'/v1/state',token_loader,pid,socket_path,
        lambda:manager.child_owned('agent',root,record,pid),**identity_options)
    if (code!=200 or not isinstance(value,dict) or value.get('status')!='verified'
            or type(value.get('on')) is not bool or type(value.get('brightnessRaw')) is not int
            or not 0<=value['brightnessRaw']<=254):
        raise ValueError('ACCEPTANCE_PHYSICAL_UNVERIFIED')
    return {'on':value['on'],'brightnessRaw':value['brightnessRaw']}


class RecoveryRelease(ManagedRelease):
    def __init__(self,root):
        super().__init__(root,None,None,observe=lambda:physical_snapshot(root),storage_ready=lambda:False)

    def exclusive(self):
        # huectl-entry acquired admission before bootstrap.run; reacquiring would deadlock.
        return nullcontext()

    def healthy(self,release):
        deadline=time.monotonic()+60
        while True:
            if super().healthy(release) is True:
                return True
            if time.monotonic()>=deadline:
                return False
            time.sleep(1)


def recover_before_start(root):
    """Under admission, settle an orphaned journal before normal startup/update."""
    path=Path(root)/'state'/NAME
    if not path.exists() and not path.is_symlink():
        return None
    journal=AcceptanceJournal(root)
    if journal.load()['phase']=='closed':
        return None
    value=recover_interrupted(RecoveryRelease(root),journal,journal.quiescent)
    if value.get('restoreVerified') is not True:
        raise RuntimeError('ACCEPTANCE_RECOVERY_UNVERIFIED')
    return value
