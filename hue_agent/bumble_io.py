import asyncio
from contextlib import AsyncExitStack

from .lamp import POWER, BRIGHTNESS, MODEL, MANUFACTURER


class BleIo:
    def __init__(self, config):
        self.config = config
        self.stack = None
        self.connection = None
        self.peer = None
        self.characteristics = {}
        self.disconnected = True

    async def connect(self):
        from bumble.core import UUID
        from bumble.device import Device, Peer
        from bumble.hci import OwnAddressType
        from bumble.keys import JsonKeyStore
        from bumble.transport import open_transport
        await self.close()
        self.stack = AsyncExitStack()
        try:
            transport = await asyncio.wait_for(open_transport(self.config['transport']), 10)
            await self.stack.enter_async_context(transport)
            device = Device.from_config_file_with_hci(self.config['device_config'], transport.source, transport.sink)
            device.le_enabled = True
            device.classic_enabled = False
            await asyncio.wait_for(device.power_on(), 10)
            if str(device.public_address) != self.config['controller_public_address']:
                raise ValueError('CONTROLLER_IDENTITY_CHANGED')
            device.keystore = JsonKeyStore.from_device(device, filename=self.config['keystore'])
            self.connection = await asyncio.wait_for(device.connect(
                self.config['address'], own_address_type=OwnAddressType.PUBLIC), 15)
            self.disconnected = False
            self.connection.on(self.connection.EVENT_DISCONNECTION, self._disconnected)
            await asyncio.wait_for(self.connection.encrypt(), 10)
            if not self.connection.is_encrypted:
                raise ConnectionError('ENCRYPTION_REQUIRED')
            self.peer = Peer(self.connection)
            control = UUID('932C32BD-0000-47A2-835A-A8D455B859DD')
            info = UUID('180A')
            services = await asyncio.wait_for(self.peer.discover_services([control, info]), 10)
            for service_uuid, fields in ((control, (POWER, BRIGHTNESS)), (info, (MODEL, MANUFACTURER))):
                matches = [s for s in services if s.uuid == service_uuid]
                if len(matches) != 1:
                    raise ValueError('SERVICE_MISSING')
                service = matches[0]
                await asyncio.wait_for(self.peer.discover_characteristics([UUID(u) for u in fields], service=service), 10)
                for field in fields:
                    matches = self.peer.get_characteristics_by_uuid(UUID(field), service)
                    if len(matches) != 1:
                        raise ValueError('CHARACTERISTIC_MISSING')
                    self.characteristics[field] = matches[0]
        except BaseException:
            await self.close()
            raise

    def _disconnected(self, reason):
        self.disconnected = True

    def _check(self, uuid):
        if self.disconnected or self.connection is None or not self.connection.is_encrypted:
            raise ConnectionError('BLE_OFFLINE')
        if uuid not in self.characteristics:
            raise ValueError('UNKNOWN_ATTRIBUTE')

    async def read(self, uuid):
        self._check(uuid)
        return bytes(await self.peer.read_value(self.characteristics[uuid]))

    async def write(self, uuid, data):
        self._check(uuid)
        if uuid not in (POWER, BRIGHTNESS) or len(data) != 1:
            raise ValueError('UNKNOWN_CONTROL')
        await self.peer.write_value(self.characteristics[uuid], data, with_response=True)

    async def close(self):
        cancelled = None
        task = asyncio.current_task()
        try:
            if self.connection is not None and not self.disconnected:
                try:
                    await asyncio.wait_for(self.connection.disconnect(), 3)
                except asyncio.CancelledError as error:
                    if task is not None and task.cancelling():
                        cancelled = error
                except Exception:
                    pass
        finally:
            self.disconnected = True
            self.connection = None
            self.peer = None
            self.characteristics = {}
            if self.stack is not None:
                stack, self.stack = self.stack, None
                try:
                    await asyncio.wait_for(stack.aclose(), 5)
                except asyncio.CancelledError as error:
                    if task is not None and task.cancelling():
                        cancelled = cancelled or error
                except Exception:
                    pass
        if cancelled is not None:
            raise cancelled
