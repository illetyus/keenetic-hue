"""One-purpose HTTPS issuer; never authenticate using a general operator token."""
import http.client
import json
import re
import ssl
from urllib.parse import urlsplit
from pathlib import Path
from .health import private_read


def issue_code():
    base=Path('/opt/var/lib/keenetic-hue-system')
    try:
        config=json.loads(private_read(base/'config/management.json'))
        if set(config)!={'publicOrigin'}:
            raise ValueError('INVALID_CONFIG')
        token=private_read(base/'secrets/setup-issuer.token').strip()
        return request_setup_code(config['publicOrigin'],token)
    except Exception:
        raise RuntimeError('SETUP_CODE_UNAVAILABLE') from None


def request_setup_code(origin, token):
    url=urlsplit(origin)
    if (url.scheme!='https' or not url.hostname or url.username or url.password
            or url.path not in ('','/') or url.query or url.fragment
            or url.port not in (None,443)):
        raise ValueError('EXACT_HTTPS_ORIGIN_REQUIRED')
    if not 32 <= len(token) <= 256 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('INVALID_ISSUER_CREDENTIAL')
    connection=None
    try:
        connection=http.client.HTTPSConnection(url.hostname,timeout=10,context=ssl.create_default_context())
        connection.request('POST','/admin/setup-code',body=b'',headers={'Authorization':'Bearer '+token})
        response=connection.getresponse();raw=response.read(4097)
        if response.status!=200 or len(raw)>4096:
            raise ValueError('ISSUER_REJECTED')
        value=json.loads(raw)
        if not re.fullmatch('[A-Z2-7]{26}',value.get('code','')) or value.get('expiresIn')!=600:
            raise ValueError('INVALID_RESPONSE')
        return {'code':value['code'],'expiresIn':600}
    except Exception:
        raise RuntimeError('SETUP_CODE_UNAVAILABLE') from None
    finally:
        if connection is not None:
            connection.close()
