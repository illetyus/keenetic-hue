from pathlib import Path

AGENT = Path('/opt/var/lib/hue-agent')
TUNNEL = Path('/opt/var/lib/hue-tunnel')
SERVICES = {'agent': Path('/opt/etc/init.d/S99hue-agent'),
            'tunnel': Path('/opt/etc/init.d/S98hue-tunnel')}
