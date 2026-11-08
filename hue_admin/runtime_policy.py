"""Authenticated runtime contracts, including explicit pre-policy compatibility.

Never infer a root fallback from missing state or a failed privilege drop. A
legacy contract exists only because its authenticated release declares the old
code layout; new runtime code requires an explicit signed metadata policy.
"""
import hashlib
import json
from pathlib import Path

from .manifest import unique_object

POLICY_NAME='metadata/runtime-policy.json'


def authenticated_manifest(root,release_id):
    from .service_manager import private_bytes,valid_id
    from .releases import verify_signed_manifest
    if not valid_id(release_id):raise ValueError('RUNTIME_RELEASE_ID')
    root=Path(root)
    raw=private_bytes(root/'releases'/release_id/'verified-manifest.json',128*1024)
    value=json.loads(raw,object_pairs_hook=unique_object)
    if not isinstance(value,dict):raise ValueError('RUNTIME_MANIFEST')
    manifest=verify_signed_manifest(raw,private_bytes(root/'config/release-trust.pub',32),
        value.get('architecture'),value.get('pythonAbi'),value.get('dependencies'))
    if manifest['releaseId']!=release_id:raise ValueError('RUNTIME_RELEASE_ID')
    return manifest


def release_policy(root,release_id):
    from .service_manager import private_bytes
    manifest=authenticated_manifest(root,release_id)
    files=manifest['files']
    if POLICY_NAME not in files:
        if any(name in files for name in ('manager/hue_admin/agent_runtime.py','manager/hue_admin/kernel_policy.py')):
            raise ValueError('RUNTIME_POLICY_REQUIRED')
        return {'schema':1,'agent':'root',
                'tunnel':'dedicated' if 'manager/hue_admin/privileges.py' in files else 'root',
                'childIdentity':False,'kernel':False}
    entry=files[POLICY_NAME]
    try:raw=private_bytes(Path(root)/'releases'/release_id/POLICY_NAME,4096)
    except OSError:raise ValueError('RUNTIME_POLICY_MISSING') from None
    if len(raw)!=entry['size'] or hashlib.sha256(raw).hexdigest()!=entry['sha256']:
        raise ValueError('RUNTIME_POLICY_HASH')
    value=json.loads(raw,object_pairs_hook=unique_object)
    if (not isinstance(value,dict) or set(value)!={'schema','agent','tunnel','childIdentity','kernel'}
            or type(value['schema']) is not int or value['schema']!=1
            or value['agent']!='dedicated' or value['tunnel']!='dedicated'
            or type(value['childIdentity']) is not bool or value['childIdentity'] is not True
            or type(value['kernel']) is not bool):
        raise ValueError('RUNTIME_POLICY_SCHEMA')
    return value
