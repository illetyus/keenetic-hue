import http.client
import json
import os
import stat
import socket
import struct
import time
from pathlib import Path
from .paths import AGENT, TUNNEL


def private_read(path, limit=8192):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > limit:
            raise ValueError('PRIVATE_FILE_REQUIRED')
        raw = os.read(fd, limit+1)
        if len(raw) > limit:
            raise ValueError('FILE_TOO_LARGE')
        return raw.decode('utf-8')
    finally:
        os.close(fd)


def process_state(pidfile, expected, proc=Path('/proc')):
    try:
        if pidfile.is_symlink():
            return 'INVALID_PID'
        pid = pidfile.read_text().strip()
        if not pid.isascii() or not pid.isdigit() or not 1 < int(pid) < 4194304:
            return 'INVALID_PID'
        command = (proc / pid / 'cmdline').read_bytes()
        return 'RUNNING' if expected in command else 'PROCESS_MISMATCH'
    except (OSError, ValueError):
        return 'PROCESS_ABSENT'


def probe(port, path, token=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        headers = {'Authorization': 'Bearer '+token} if token else {}
        connection.request('GET', path, headers=headers)
        response = connection.getresponse()
        raw = response.read(8193)
        if len(raw) > 8192:
            raise ValueError('BODY_TOO_LARGE')
        return response.status, json.loads(raw) if port == 8787 else {}
    finally:
        connection.close()


def verify_tcp_peer(connection,pid):
    """Match this established TCP connection to an accepted FD of the owned agent.

    Verifying only a listener or a PID before connect leaves a port-rebind race.
    An established connection cannot be transferred to a newly bound listener.
    """
    if type(pid) is not int or pid<=1:return False
    client=connection.sock.getsockname();server=connection.sock.getpeername()
    if client[0]!='127.0.0.1' or server[0]!='127.0.0.1':return False
    address=f"{struct.unpack('=I',socket.inet_aton('127.0.0.1'))[0]:08X}"
    local=f'{address}:{server[1]:04X}';remote=f'{address}:{client[1]:04X}'
    for _ in range(50):
        try:
            inodes={line.split()[9] for line in Path('/proc/net/tcp').read_text().splitlines()[1:]
                    if len(line.split())>9 and line.split()[1:4]==[local,remote,'01'] and line.split()[9]!='0'}
            if inodes:
                for fd in (Path('/proc')/str(pid)/'fd').iterdir():
                    try:
                        if os.readlink(fd) in {f'socket:[{i}]' for i in inodes}:return True
                    except OSError:pass
        except OSError:return False
        time.sleep(.01)
    return False


def verify_process_identity(pid,expected_uid,expected_gid):
    try:
        if type(pid) is not int or pid<=1:return False
        rows=dict(line.split(':',1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
        return (list(map(int,rows['Uid'].split()))==[expected_uid]*4
                and list(map(int,rows['Gid'].split()))==[expected_gid]*4)
    except (OSError,ValueError,KeyError):return False


def authenticated_probe(port,path,token_loader,pid,unix_path=None,peer_guard=lambda:True,*,expected_uid=None,expected_gid=None):
    expected_uid=getattr(os,'geteuid',lambda:0)() if expected_uid is None else expected_uid
    expected_gid=getattr(os,'getegid',lambda:0)() if expected_gid is None else expected_gid
    if any(type(value) is not int or not 0<=value<=2147483647 for value in (expected_uid,expected_gid)):
        raise ValueError('HEALTH_IDENTITY')
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    try:
        if unix_path is not None and (unix_path.exists() or unix_path.is_symlink()):
            info=unix_path.lstat();parent=unix_path.parent.lstat()
            if (not stat.S_ISSOCK(info.st_mode) or info.st_uid!=expected_uid or info.st_gid!=expected_gid
                    or stat.S_IMODE(info.st_mode)!=0o600 or not stat.S_ISDIR(parent.st_mode)
                    or stat.S_IMODE(parent.st_mode)!=0o700 or parent.st_uid!=expected_uid or parent.st_gid!=expected_gid):
                raise ValueError('HEALTH_PEER')
            sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);sock.settimeout(5);connection.sock=sock;sock.connect(str(unix_path))
            observed_pid,uid,gid=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            valid=observed_pid==pid and uid==expected_uid and gid==expected_gid
        else:
            connection.connect();valid=verify_tcp_peer(connection,pid) and verify_process_identity(pid,expected_uid,expected_gid)
        if not valid or not peer_guard():raise ValueError('HEALTH_PEER')
        token=token_loader()
        connection.request('GET',path,headers={'Authorization':'Bearer '+token})
        response=connection.getresponse();raw=response.read(8193)
        if len(raw)>8192:raise ValueError('BODY_TOO_LARGE')
        return response.status,json.loads(raw)
    finally:connection.close()


def status(live=False):
    system=Path('/opt/var/lib/keenetic-hue-system')
    if (system/'active.json').exists() or (system/'active.json').is_symlink() or (system/'state/schema.json').exists() or (system/'state/initialize.json').exists():
        from .unified_health import status as unified_status
        return unified_status(live,system)
    now = time.time()
    def item(state, reason):
        return {'state': state, 'observedAt': now, 'reason': reason}
    agent = process_state(AGENT/'agent.pid', b'-m\0hue_agent\0--config\0/opt/var/lib/hue-agent/config.json')
    tunnel = process_state(TUNNEL/'tunnel.pid', b'/opt/var/lib/hue-tunnel/cloudflared\0tunnel\0')
    supervisor = process_state(TUNNEL/'supervisor.pid', b'/opt/etc/init.d/S98hue-tunnel\0supervise')
    components = {name: item('running' if reason == 'RUNNING' else 'stopped', reason)
                  for name, reason in (('agent', agent), ('tunnel', tunnel), ('manager', supervisor))}
    for name in ('localApi', 'ble', 'reporting'):
        components[name] = item('unknown', 'NOT_CHECKED')
    if live:
        try:
            cfg = json.loads(private_read(AGENT/'config.json'))
            # Fixed deployment identity: arbitrary config paths cannot disclose files in output.
            token_path = Path(cfg['token_file'])
            if token_path != AGENT/'api.token':
                raise ValueError('UNEXPECTED_TOKEN_PATH')
            pid=int((AGENT/'agent.pid').read_text().strip())
            code,value=authenticated_probe(8787,'/v1/state',lambda:private_read(token_path).strip(),pid,
                peer_guard=lambda:process_state(AGENT/'agent.pid',b'-m\0hue_agent\0--config\0/opt/var/lib/hue-agent/config.json')=='RUNNING')
            components['localApi'] = item('ready' if code in (200, 503) else 'degraded', 'API_RESPONSE' if code in (200,503) else 'API_DENIED')
            components['ble'] = item('ready' if code == 200 and value.get('status') == 'verified' else 'degraded', 'VERIFIED' if code == 200 and value.get('status') == 'verified' else 'BLE_UNAVAILABLE')
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            components['localApi'] = item('degraded', 'LOCAL_CHECK_FAILED')
            components['ble'] = item('unknown', 'LOCAL_CHECK_FAILED')
        try:
            code, _ = probe(18790, '/ready')
            components['tunnel'] = item('ready' if code == 200 else 'degraded', 'TUNNEL_READY' if code == 200 else 'TUNNEL_NOT_READY')
        except (OSError, ValueError, http.client.HTTPException):
            components['tunnel'] = item('degraded', 'TUNNEL_UNREACHABLE')
    required = ('agent', 'manager', 'tunnel') + (('localApi', 'ble') if live else ())
    return {'schemaVersion': 1, 'healthy': all(components[n]['state'] in ('running','ready') for n in required), 'components': components}
