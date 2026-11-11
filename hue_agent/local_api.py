import asyncio
import hmac
import json
import os
import stat


class LocalApi:
    def __init__(self, controller, token):
        self.controller = controller
        self.token = token
        self.active = 0
        self.pending_headers = {}
        self.tasks = set()
        self.server = None
        self.unix_server=None
        self.unix_path=None
        self.closing = False

    async def start(self, port=8787):
        self.server = await asyncio.start_server(self._client, '127.0.0.1', port, limit=8192)
        return self.server

    async def start_private(self,path):
        parent=path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_mode&0o077 or parent.st_uid!=os.geteuid():
            raise ValueError('HEALTH_PRIVATE_DIRECTORY')
        if path.exists() or path.is_symlink():
            info=path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.geteuid():raise ValueError('HEALTH_SOCKET_TYPE')
            # Lifecycle lock guarantees this agent is the sole owner before unlink.
            path.unlink()
        self.unix_server=await asyncio.start_unix_server(self._client,str(path),limit=8192)
        os.chmod(path,0o600);self.unix_path=path

    async def _client(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            if not self.closing:
                await self.handle(reader, writer)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.tasks.discard(task)

    async def close(self):
        self.closing = True
        if self.server is not None:
            self.server.close()
        if self.unix_server is not None:self.unix_server.close()
        # Cancel accepted clients while their BLE/journal dependencies are still live.
        await asyncio.sleep(0)
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()
        if self.unix_server is not None:
            await self.unix_server.wait_closed()
            if self.unix_path:self.unix_path.unlink(missing_ok=True)

    async def _read_headers(self, reader):
        task = asyncio.current_task()
        # New arrivals can authenticate even when old clients withhold headers.
        # Evict only incomplete header readers, never authenticated operations.
        if len(self.pending_headers) >= 16:
            oldest = next(iter(self.pending_headers))
            self.pending_headers.pop(oldest)
            oldest.cancel()
        self.pending_headers[task] = None
        try:
            return await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 3)
        finally:
            self.pending_headers.pop(task, None)

    async def _dispatch(self, method, path, version, headers, reader):
        if self.active >= 16:
            return '503 Service Unavailable', {'errorCode': 'BUSY'}
        self.active += 1
        try:
            if 'transfer-encoding' in headers or version != 'HTTP/1.1':
                raise ValueError('UNSUPPORTED_HTTP')
            if method == 'GET' and path == '/v1/state':
                result = await self.controller.query()
                return ('200 OK' if result['status'] == 'verified' else '503 Service Unavailable'), result
            if method == 'POST' and path == '/v1/commands':
                if 'content-length' not in headers:
                    return '411 Length Required', {'errorCode': 'LENGTH_REQUIRED'}
                length = int(headers['content-length'])
                if length <= 0:
                    return '400 Bad Request', {'errorCode': 'INVALID_SIZE'}
                if length > 4096:
                    return '413 Payload Too Large', {'errorCode': 'INVALID_SIZE'}
                body = await asyncio.wait_for(reader.readexactly(length), 3)
                return '200 OK', await self.controller.execute(json.loads(body))
            return '404 Not Found', {'errorCode': 'NOT_FOUND'}
        finally:
            self.active -= 1

    async def handle(self, reader, writer):
        status, result = '400 Bad Request', {'errorCode': 'BAD_REQUEST'}
        try:
            raw = await self._read_headers(reader)
            if len(raw) > 8192:
                raise ValueError('HEADERS_TOO_LARGE')
            lines = raw.decode('ascii').split('\r\n')
            method, path, version = lines[0].split(' ')
            headers = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, value = line.split(':', 1)
                key = key.lower().strip()
                if key in headers:
                    raise ValueError('DUPLICATE_HEADER')
                headers[key] = value.strip()
            if not hmac.compare_digest(headers.get('authorization', ''), 'Bearer '+self.token):
                status, result = '401 Unauthorized', {'errorCode': 'UNAUTHORIZED'}
            else:
                status, result = await self._dispatch(method, path, version, headers, reader)
        except (ValueError, UnicodeError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            status, result = '400 Bad Request', {'errorCode': 'BAD_REQUEST'}
        except Exception:
            status, result = '500 Internal Server Error', {'errorCode': 'INTERNAL_ERROR'}
        try:
            body = json.dumps(result, allow_nan=False, separators=(',', ':')).encode()
            writer.write(f'HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'.encode()+body)
            await asyncio.wait_for(writer.drain(), 3)
        except (ConnectionError, TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
