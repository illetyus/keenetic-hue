import argparse
import asyncio
import contextlib
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import signal
import time
from pathlib import Path

from .agent import Controller
from .bumble_io import BleIo
from .journal import Journal
from .local_api import LocalApi
from .events import emit
from .private_files import private_file,private_health_path


def configure_logging(log_file):
    handler = RotatingFileHandler(log_file, maxBytes=131072, backupCount=2)
    formatter = logging.Formatter('%(asctime)sZ %(levelname)s:%(name)s:%(message)s', datefmt='%Y-%m-%dT%H:%M:%S')
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)
    logging.getLogger('bumble').setLevel(logging.CRITICAL)


async def run(config):
    token = private_file(config['token_file']).read_text().strip()
    if len(token) < 32 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError('INVALID_TOKEN_FILE')
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    journal = None
    io = BleIo(config)
    api = None
    monitor = None
    cloud = None
    cloud_task = None
    tunnel_api = None
    try:
        journal = Journal(config['journal'])
        controller = Controller(io, journal)
        api = LocalApi(controller, token)
        await api.start(config.get('port',8787))
        health=private_health_path(config)
        if health is not None:await api.start_private(health)
        monitor = asyncio.create_task(controller.monitor())
        tunnel_config = config.get('tunnel')
        if isinstance(tunnel_config,dict) and tunnel_config.get('enabled') is True:
            try:
                from .tunnel_api import TunnelApi
                tunnel_token = private_file(tunnel_config['token_file']).read_text().strip()
                if not 32 <= len(tunnel_token) <= 256 or not tunnel_token.isascii() or any(c.isspace() for c in tunnel_token):
                    raise ValueError('INVALID_TUNNEL_TOKEN')
                tunnel_api = TunnelApi(controller,tunnel_token)
                await tunnel_api.start(tunnel_config.get('port',8788))
            except Exception as error:
                if tunnel_api is not None:
                    with contextlib.suppress(Exception):
                        await tunnel_api.close()
                    tunnel_api = None
                emit('tunnel_disabled',errorType=type(error).__name__)
        cloud_config = config.get('cloud')
        if 'cloud' in config and not isinstance(cloud_config, dict):
            emit('cloud_disabled', errorType='INVALID_CLOUD_CONFIG')
        elif isinstance(cloud_config, dict) and cloud_config.get('enabled') is True:
            try:
                from .cloud_link import CloudLink
                cloud = CloudLink(controller, cloud_config)
                cloud_task = asyncio.create_task(cloud.run())
            except Exception as error:
                emit('cloud_disabled', errorType=type(error).__name__)
        print(json.dumps({'event':'api_started','bind':'127.0.0.1', 'port':config.get('port',8787), 'pid':os.getpid()}), flush=True)
        await stop.wait()
    finally:
        try:
            if tunnel_api is not None:
                with contextlib.suppress(Exception):
                    await tunnel_api.close()
            if cloud is not None:
                with contextlib.suppress(Exception):
                    await cloud.close()
                if cloud_task is not None:
                    cloud_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await cloud_task
            if api is not None:
                with contextlib.suppress(Exception):
                    await api.close()
            if monitor is not None:
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await monitor
            with contextlib.suppress(Exception):
                await io.close()
        finally:
            if journal is not None:
                journal.close()
            print(json.dumps({'event':'agent_stopped'}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    config = json.loads(private_file(args.config).read_text())
    configure_logging(config['log_file'])
    private_file(config['keystore'])
    lock = open(config['lock_file'], 'a+')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('AGENT_ALREADY_RUNNING')
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    # Publish the service PID only after this process owns the adapter lock.
    pid_file = Path(config['pid_file']) if config.get('pid_file') else None
    if pid_file is not None:
        temporary = pid_file.with_suffix('.pid.tmp')
        temporary.write_text(str(os.getpid()))
        temporary.replace(pid_file)
    try:
        asyncio.run(run(config))
    except Exception as error:
        print(json.dumps({'event':'agent_failed', 'errorType':type(error).__name__}), flush=True)
        raise SystemExit(1)
    finally:
        if pid_file is not None and pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
            pid_file.unlink()
        lock.close()


if __name__ == '__main__':
    main()
