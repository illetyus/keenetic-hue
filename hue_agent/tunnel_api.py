"""Narrow loopback ingress for the authenticated Workers VPC Service."""
import time
import uuid
from .local_api import LocalApi
from .protocol import Command, ExecutionContext, ProtocolError


class TunnelController:
    def __init__(self, controller, context):
        self.original = controller
        self.context = context

    async def query(self):
        result = await self.original.query()
        return dict(result, sessionId=self.context.session_id)

    async def execute(self, data):
        try:
            command = Command.parse(data, self.context.session_id, allow_expired=True)
            if not 1 <= command.sequence <= 2**53-1 or command.expires_at > time.time()+6:
                raise ProtocolError('INVALID_COMMAND')
        except ProtocolError as error:
            return {'commandId':data.get('commandId') if isinstance(data,dict) else None,
                    'status':'error','errorCode':error.code}
        return await self.original.execute(data, context=self.context)


class TunnelApi(LocalApi):
    def __init__(self, controller, token):
        self.live = True
        self.generation = 0
        self.context = self._context()
        super().__init__(TunnelController(controller,self.context),token)

    def _context(self):
        generation = self.generation
        return ExecutionContext(str(uuid.uuid4()), lambda:self.live and self.generation == generation)

    def revoke(self):
        self.generation += 1
        self.context = self._context()
        self.controller.context = self.context

    async def _dispatch(self, method, path, version, headers, reader):
        if method == 'POST' and path == '/v1/revoke':
            if version != 'HTTP/1.1' or 'transfer-encoding' in headers or headers.get('content-length','0') != '0':
                raise ValueError('UNSUPPORTED_HTTP')
            self.revoke()
            return '200 OK', {'revoked':True}
        if method == 'GET' and path == '/v1/session':
            if version != 'HTTP/1.1' or 'transfer-encoding' in headers:
                raise ValueError('UNSUPPORTED_HTTP')
            return '200 OK', {'sessionId':self.context.session_id,'serverTime':time.time()}
        return await super()._dispatch(method,path,version,headers,reader)

    async def close(self):
        self.live = False
        await super().close()
