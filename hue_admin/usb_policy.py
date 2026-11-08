"""Select only the pinned Belkin adapter; never consume an event-supplied path.

Selection is pure. A later root permission preparer must independently verify
the actual node's character-device type, rdev and inode before changing modes.
Serial matching narrows selection; it is not cryptographic hardware identity.
"""
import re


def _validate_policy(policy):
    if (not isinstance(policy,dict) or set(policy)!={'vendor','product','serial'}
            or policy['vendor']!='050d' or policy['product']!='065a'
            or not isinstance(policy['serial'],str)
            or not re.fullmatch('[0-9A-Fa-f]{12}',policy['serial'])):
        raise ValueError('USB_POLICY_INVALID')


def pinned_transport(policy):
    """Bumble0.0.236 supports VID:PID/serial!; retain actual descriptor case."""
    _validate_policy(policy)
    return f"usb:{policy['vendor']}:{policy['product']}/{policy['serial']}!"


def target_for_usb(devices,policy):
    _validate_policy(policy)
    if not isinstance(devices,list):raise ValueError('USB_INVENTORY_INVALID')
    matches=[]
    for item in devices:
        if not isinstance(item,dict):raise ValueError('USB_INVENTORY_INVALID')
        if (item.get('vendor')!=policy['vendor'] or item.get('product')!=policy['product']
                or not isinstance(item.get('serial'),str)
                or item['serial'].upper()!=policy['serial'].upper()):
            continue
        bus,device,name=item.get('bus'),item.get('device'),item.get('sysfsName')
        if (type(bus) is not int or not 1<=bus<=255
                or type(device) is not int or not 1<=device<=127
                or not isinstance(name,str)):
            raise ValueError('USB_DEVICE_METADATA')
        topology=re.fullmatch('([1-9][0-9]*)-[1-9][0-9]*(?:\\.[1-9][0-9]*)*',name)
        if not topology or int(topology.group(1))!=bus:
            raise ValueError('USB_DEVICE_TOPOLOGY')
        matches.append({'path':f'/dev/bus/usb/{bus:03d}/{device:03d}',
                        'major':189,'minor':(bus-1)*128+device-1,'sysfsName':name})
    if len(matches)!=1:
        raise ValueError('USB_DEVICE_UNAVAILABLE' if not matches else 'USB_DEVICE_AMBIGUOUS')
    return matches[0]
