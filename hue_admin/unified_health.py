"""Read-only ownership, version and physical health for the unified root."""
import http.client
import time
from pathlib import Path
from . import service_manager as manager
from .health import verified_probe, authenticated_probe
from .lifecycle_events import unavailable_reason


def _component_identity(root, component, release_id):
    """Resolve the policy mode and expected UID/GID contract for a managed component."""
    from .runtime_policy import release_policy
    mode = release_policy(root, release_id)[component]
    if mode == 'dedicated':
        from .privileges import load_service_identity
        return mode, load_service_identity(root, 'hue-' + component)
    return mode, {'uid': 0, 'gid': 0}


def status(live=False,root=manager.SYSTEM):
    root=Path(root);now=time.time()
    def item(state,reason):return {'state':state,'observedAt':now,'reason':reason}
    components={};owned={};selected=None
    try:selected=manager.selection(root)
    except (OSError,ValueError,KeyError):pass
    for component in ('agent','tunnel'):
        good=False;record=None
        try:
            record=manager.owner_record(component,root)
            pid=manager.child_pid(component,root)
            good=bool(selected and record and record['releaseId']==selected
                      and manager.supervisor_owned(component,root,record)
                      and pid and manager.child_owned(component,root,record,pid))
        except (OSError,ValueError,KeyError,UnicodeError):pass
        owned[component]=good
        components[component]=item('running' if good else 'degraded','OWNED_VERSION' if good else unavailable_reason(root,component,record,selected))
    version_ok=all(owned.values())
    components['manager']=item('running' if version_ok else 'degraded','BOTH_COMPONENTS_OWNED' if version_ok else 'OWNER_CHECK_FAILED')
    for name in ('localApi','ble','reporting'):components[name]=item('unknown','NOT_CHECKED')
    physical=True
    if live:
        try:
            if not owned['agent']:raise ValueError('AGENT_OWNER')
            def token_loader():
                token=manager.private_bytes(root/'secrets/api.token',512).decode('ascii').strip()
                if not 32<=len(token)<=256 or any(c.isspace() for c in token):raise ValueError('API_TOKEN')
                return token
            pid=manager.child_pid('agent',root);record=manager.owner_record('agent',root)
            if not pid or not record:raise ValueError('AGENT_OWNER')
            mode,identity=_component_identity(root,'agent',record['releaseId'])
            socket_path=root/'run/health.sock';identity_options={}
            if mode=='dedicated':
                from .agent_storage import agent_runtime_paths
                socket_path=agent_runtime_paths(root,record['releaseId'])['health_socket']
                identity_options={'expected_uid':identity['uid'],'expected_gid':identity['gid']}
            code,value=authenticated_probe(8787,'/v1/state',token_loader,pid,socket_path,
                lambda:manager.child_owned('agent',root,record,pid),**identity_options)
            if not isinstance(value,dict):raise ValueError('INVALID_API_RESPONSE')
            api=code in (200,503)
            ble=code==200 and value.get('status')=='verified'
            components['localApi']=item('ready' if api else 'degraded','API_RESPONSE' if api else 'API_DENIED')
            components['ble']=item('ready' if ble else 'degraded','VERIFIED' if ble else 'BLE_UNAVAILABLE')
            physical=api and ble
        except (OSError,ValueError,TypeError,AttributeError,http.client.HTTPException):
            components['localApi']=item('degraded','LOCAL_CHECK_FAILED')
            components['ble']=item('unknown','LOCAL_CHECK_FAILED')
            physical=False
        try:
            if not owned['tunnel']:raise ValueError('TUNNEL_OWNER')
            tunnel_pid=manager.child_pid('tunnel',root);tunnel_record=manager.owner_record('tunnel',root)
            if not tunnel_pid or not tunnel_record:raise ValueError('TUNNEL_OWNER')
            _,identity=_component_identity(root,'tunnel',tunnel_record['releaseId'])
            code,_=verified_probe(18790,'/ready',tunnel_pid,
                lambda:manager.child_owned('tunnel',root,tunnel_record,tunnel_pid),
                expected_uid=identity['uid'],expected_gid=identity['gid'])
            ready=code==200 and owned['tunnel']
            components['tunnel']=item('ready' if ready else 'degraded',
                                     'TUNNEL_READY' if ready else 'TUNNEL_OWNER_OR_READINESS_FAILED')
            physical=physical and ready
        except (OSError,ValueError,KeyError,TypeError,AttributeError,http.client.HTTPException) as error:
            if not owned['tunnel']:
                reason=components['tunnel']['reason']
            elif isinstance(error,ValueError) and str(error) in ('HEALTH_PEER','HEALTH_IDENTITY','TUNNEL_OWNER'):
                reason='TUNNEL_OWNER_OR_READINESS_FAILED'
            else:
                reason='TUNNEL_UNREACHABLE'
            components['tunnel']=item('degraded',reason);physical=False
    principals={component:{'expectedUid':None,'expectedGid':None} for component in ('agent','tunnel')}
    if selected:
        try:
            expected={}
            for component in ('agent','tunnel'):
                _,identity=_component_identity(root,component,selected)
                expected[component]={'expectedUid':identity['uid'],'expectedGid':identity['gid']}
            principals=expected
        except (OSError,ValueError,KeyError,TypeError):pass
    bootstrap={'bootstrapReleaseId':None,'bootstrapInSync':False}
    try:
        from .bootstrap_install import bootstrap_sync_status
        bootstrap=bootstrap_sync_status(root,selected)
    except (OSError,ValueError,KeyError,TypeError):pass
    return {'schemaVersion':1,'healthy':version_ok and physical,'releaseId':selected if version_ok else None,
            **bootstrap,'components':components,'principals':principals}
