import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from hue_agent.local_api import LocalApi


class ApiSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_body_wait_survives_header_pressure(self):
        class Controller:
            async def execute(self, body):
                return {'status': 'verified', 'received': body}
        accepted = asyncio.Event()
        class ObservedApi(LocalApi):
            arrivals = 0
            async def _client(self, reader, writer):
                self.arrivals += 1
                if self.arrivals == 18:
                    accepted.set()
                await super()._client(reader, writer)
        api = ObservedApi(Controller(), 'test-token')
        server = await api.start(0)
        port = server.sockets[0].getsockname()[1]
        clients = []
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            clients.append(writer)
            writer.write(b'POST /v1/commands HTTP/1.1\r\nAuthorization: Bearer test-token\r\nContent-Length: 2\r\n\r\n{')
            await writer.drain()
            await asyncio.sleep(0.03)
            connections = await asyncio.gather(*(asyncio.open_connection('127.0.0.1', port) for _ in range(17)))
            for _, slow in connections:
                clients.append(slow)
                slow.write(b'GET /v1/state HTTP/1.1\r\nAuthorization: Bearer ')
                await slow.drain()
            await asyncio.wait_for(accepted.wait(), 1)
            writer.write(b'}')
            await writer.drain()
            reply = await asyncio.wait_for(reader.read(), 1)
            self.assertIn(b'200 OK', reply)
            self.assertIn(b'"received":{}', reply)
        finally:
            await api.close()
            for writer in clients:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

    async def test_slow_unauthenticated_headers_do_not_block_authorized_query(self):
        class Controller:
            async def query(self):
                return {'status': 'verified', 'on': True}
        api = LocalApi(Controller(), 'test-token')
        server = await api.start(0)
        port = server.sockets[0].getsockname()[1]
        clients = []
        try:
            for _ in range(16):
                reader, writer = await asyncio.open_connection('127.0.0.1', port)
                clients.append(writer)
                writer.write(b'GET /v1/state HTTP/1.1\r\n')
                await writer.drain()
            await asyncio.sleep(0.03)
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            clients.append(writer)
            writer.write(b'GET /v1/state HTTP/1.1\r\nAuthorization: Bearer test-token\r\n\r\n')
            await writer.drain()
            try:
                reply = await asyncio.wait_for(reader.read(), 1)
            except ConnectionError:
                reply = b''
            self.assertIn(b'200 OK', reply)
            self.assertIn(b'"on":true', reply)
        finally:
            await api.close()
            for writer in clients:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass


class InstallerSecurityTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('installer', Path(__file__).parents[1]/'deploy'/'install.py')
        self.installer = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(self.installer)
        except Exception as error:
            self.fail(f'Installer must be import-safe for integrity validation: {type(error).__name__}')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name)
        self.inputs = {'hue_agent/__init__.py': b'# trusted', 'deploy/install.py': b'# installer',
                       'deploy/launch.py': b'# launch', 'deploy/S99hue-agent': b'# init',
                       'config.json': b'{}', 'api.token': b'test-only-token',
                       'acceptance-passed.json': b'{}'}
        for name, data in self.inputs.items():
            path = self.stage/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        raw = json.dumps({name: hashlib.sha256(data).hexdigest() for name, data in self.inputs.items()}).encode()
        (self.stage/'install-manifest.json').write_bytes(raw)
        self.digest = hashlib.sha256(raw).hexdigest()

    def test_verified_snapshot_does_not_reread_mutated_source(self):
        snapshot = self.installer.load_snapshot(self.stage, self.digest)
        (self.stage/'hue_agent/__init__.py').write_bytes(b'# attacker')
        self.assertEqual(snapshot['hue_agent/__init__.py'], b'# trusted')

    def test_install_uses_verified_snapshot_and_refuses_overwrite(self):
        snapshot = dict(self.inputs)
        snapshot['acceptance-passed.json'] = json.dumps({'restored': True, 'controls': 50, 'queries': 50,
            'reconnections': 5, 'max_rss_kib': 32000, 'min_available_kib': 120000, 'dns_failures': 0}).encode()
        target, startup = self.stage/'installed', self.stage/'init-script'
        self.installer.install_snapshot(snapshot, target, startup)
        self.assertEqual((target/'app/hue_agent/__init__.py').read_bytes(), b'# trusted')
        self.assertEqual((target/'api.token').read_bytes(), b'test-only-token')
        self.assertEqual(startup.read_bytes(), b'# init')
        config = json.loads((target/'config.json').read_text())
        self.assertEqual(config['token_file'], str(target/'api.token'))
        with self.assertRaises(SystemExit):
            self.installer.install_snapshot(snapshot, target, startup)

    def test_tampered_input_is_rejected(self):
        for name in ('deploy/launch.py', 'config.json', 'api.token', 'acceptance-passed.json', 'deploy/install.py'):
            with self.subTest(name=name):
                path = self.stage/name
                path.write_bytes(b'changed')
                with self.assertRaises(ValueError):
                    self.installer.load_snapshot(self.stage, self.digest)
                path.write_bytes(self.inputs[name])

    def test_replaced_manifest_is_rejected(self):
        (self.stage/'install-manifest.json').write_text('{}')
        with self.assertRaises(ValueError):
            self.installer.load_snapshot(self.stage, self.digest)

    def test_extra_application_file_is_rejected(self):
        (self.stage/'hue_agent/extra.py').write_text('# injected')
        with self.assertRaises(ValueError):
            self.installer.load_snapshot(self.stage, self.digest)
