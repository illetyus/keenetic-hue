"""Select a verified release only after stop; rollback on failed version health."""
import json
import hashlib
from pathlib import Path
import re

from .installation import install_lock, slot_verified
from .manifest import unique_object
from .releases import verify_signed_manifest
from .restore_transaction import atomic_replace,sync_directory
from .snapshot import read_private_bytes


def valid_id(value):
    return isinstance(value,str) and re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,40})?',value)


def validate_pointer(value):
    if (not isinstance(value,dict) or type(value.get('schema')) is not int or value['schema']!=1
            or set(value)-{'schema','releaseId','previousReleaseId'} or not valid_id(value.get('releaseId'))
            or 'previousReleaseId' in value and not valid_id(value['previousReleaseId'])):
        raise ValueError('ACTIVATION_POINTER')
    return value


def pointer(root):
    path=root/'active.json'
    if path.is_symlink():raise ValueError('ACTIVATION_POINTER')
    if not path.exists():return None
    return validate_pointer(json.loads(read_private_bytes(path,4096),object_pairs_hook=unique_object))


def verify_slot(root,release_id,key,runtime):
    if not valid_id(release_id):raise ValueError('ACTIVATION_RELEASE_ID')
    if read_private_bytes(root/'config/release-trust.pub',32)!=key:raise ValueError('ACTIVATION_TRUST_KEY_CHANGED')
    slot=root/'releases'/release_id
    raw=read_private_bytes(slot/'verified-manifest.json',128*1024)
    manifest=verify_signed_manifest(raw,key,*runtime)
    if manifest['releaseId']!=release_id or not slot_verified(slot,raw):raise ValueError('ACTIVATION_SLOT_HASH')
    return slot


def check_schema(root,slot):
    current=json.loads(read_private_bytes(root/'state/schema.json',4096),object_pairs_hook=unique_object)
    supported=json.loads(read_private_bytes(slot/'metadata/state-schema.json',4096),object_pairs_hook=unique_object)
    if (current!={'schema':1} or type(current.get('schema')) is not int or not isinstance(supported,dict) or set(supported)!={'schema','read','write'}
            or type(supported['schema']) is not int or supported['schema']!=1
            or type(supported['write']) is not int or supported['write']!=1
            or not isinstance(supported['read'],list) or not 1<=len(supported['read'])<=4
            or any(type(v) is not int for v in supported['read']) or 1 not in supported['read']):
        raise ValueError('ACTIVATION_STATE_INCOMPATIBLE')


def save_receipt(root,value):
    atomic_replace(root/'state/activation.json',json.dumps(value,sort_keys=True).encode())


def release_order(root,raw,key,runtime,commit=False):
    manifest=verify_signed_manifest(raw,key,*runtime)
    seq=manifest.get('releaseSequence',0)
    digest=hashlib.sha256(raw).hexdigest()
    path=root/'state/release-order.json'
    highest=None
    if path.exists() or path.is_symlink():
        highest=json.loads(read_private_bytes(path,4096),object_pairs_hook=unique_object)
        if (not isinstance(highest,dict) or set(highest)!={'sequence','digest'}
                or type(highest['sequence']) is not int or not 1<=highest['sequence']<2**53
                or not isinstance(highest['digest'],str) or not re.fullmatch('[a-f0-9]{64}',highest['digest'])):
            raise ValueError('RELEASE_ORDER_STATE')
    if not seq:
        if commit:return
        raise ValueError('RELEASE_SEQUENCE_REQUIRED')
    if highest and (seq<highest['sequence'] or seq==highest['sequence'] and digest!=highest['digest']):
        if commit and seq<highest['sequence']:return  # Explicit rollback never lowers the floor.
        raise ValueError('RELEASE_DOWNGRADE_FORBIDDEN')
    if commit:atomic_replace(path,json.dumps({'sequence':seq,'digest':digest},sort_keys=True).encode())


def receipt(root):
    path=root/'state/activation.json'
    if path.is_symlink():raise ValueError('ACTIVATION_RECEIPT')
    if not path.exists():return None
    value=json.loads(read_private_bytes(path,4096),object_pairs_hook=unique_object)
    if (not isinstance(value,dict) or set(value)!={'schema','phase','before','newReleaseId'}
            or type(value['schema']) is not int or value['schema']!=1
            or value['phase'] not in ('prepared','stopped','selected','started','committed','rolled_back')
            or not valid_id(value['newReleaseId'])):raise ValueError('ACTIVATION_RECEIPT')
    if value['before'] is not None:validate_pointer(value['before'])
    return value


def _recover(root,key,runtime,stop,start):
    value=receipt(root)
    if value is None or value['phase']=='rolled_back':return
    if value['phase']=='committed':
        verify_slot(root,value['newReleaseId'],key,runtime)
        release_order(root,read_private_bytes(root/'releases'/value['newReleaseId']/'verified-manifest.json',128*1024),key,runtime,True)
        return
    before=value['before']
    if before is not None:
        slot=verify_slot(root,before['releaseId'],key,runtime);check_schema(root,slot)
    current=pointer(root)
    if current is not None and current['releaseId'] not in (value['newReleaseId'],before['releaseId'] if before else None):
        raise ValueError('ACTIVATION_SELECTION_CHANGED')
    if value['phase']!='prepared' or current!=before:
        if not stop():raise RuntimeError('ACTIVATION_RECOVERY_STOP_FAILED')
        if before is None:
            (root/'active.json').unlink(missing_ok=True);sync_directory(root)
        else:atomic_replace(root/'active.json',json.dumps(before,sort_keys=True).encode())
    if before is not None:
        if not start():raise RuntimeError('ACTIVATION_RECOVERY_START_FAILED')
    value['phase']='rolled_back';save_receipt(root,value)


def recover_activation(root,key,runtime,stop,start):
    root=Path(root).absolute()
    with install_lock(root):_recover(root,key,runtime,stop,start)
    return {'recoveryComplete':True}


def activate_candidate(root,release_id,key,runtime,stop,start,health,failpoint=lambda phase:None,expected_current=None):
    root=Path(root).absolute()
    with install_lock(root):
        previous_receipt=receipt(root)
        if previous_receipt and previous_receipt['phase'] not in ('committed','rolled_back'):
            raise ValueError('ACTIVATION_RECOVERY_REQUIRED')
        candidate=verify_slot(root,release_id,key,runtime);check_schema(root,candidate)
        before=pointer(root)
        candidate_raw=read_private_bytes(candidate/'verified-manifest.json',128*1024)
        if expected_current is None:
            if before is not None:
                release_order(root,read_private_bytes(root/'releases'/before['releaseId']/'verified-manifest.json',128*1024),key,runtime,True)
            release_order(root,candidate_raw,key,runtime)
        if expected_current is not None and before!=expected_current:raise ValueError('ROLLBACK_SELECTION_CHANGED')
        if before is not None:
            previous=verify_slot(root,before['releaseId'],key,runtime);check_schema(root,previous)
            if before['releaseId']==release_id:raise ValueError('ACTIVATION_ALREADY_SELECTED')
        value={'schema':1,'phase':'prepared','before':before,'newReleaseId':release_id}
        def record(phase):
            value['phase']=phase;save_receipt(root,value);failpoint(phase)
        record('prepared')
        try:
            if not stop():raise RuntimeError('ACTIVATION_STOP_FAILED')
            record('stopped')
            selection={'schema':1,'releaseId':release_id}
            if before is not None:selection['previousReleaseId']=before['releaseId']
            atomic_replace(root/'active.json',json.dumps(selection,sort_keys=True).encode())
            record('selected')
            if not start():raise RuntimeError('ACTIVATION_START_FAILED')
            record('started')
            observed=health()
            if not isinstance(observed,dict) or observed.get('healthy') is not True or observed.get('releaseId')!=release_id:
                raise RuntimeError('ACTIVATION_VERSION_HEALTH_FAILED')
            record('committed')
            release_order(root,candidate_raw,key,runtime,True)
        except Exception:
            _recover(root,key,runtime,stop,start);raise
    return {'activated':True,'releaseId':release_id,'versionHealthVerified':True}


def rollback(root,key,runtime,stop,start,health):
    before=pointer(Path(root))
    if before is None or 'previousReleaseId' not in before:raise ValueError('ROLLBACK_NO_PREVIOUS_RELEASE')
    return activate_candidate(root,before['previousReleaseId'],key,runtime,stop,start,health,expected_current=before)
