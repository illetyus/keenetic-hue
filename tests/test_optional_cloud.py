import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch

from hue_agent.local_api import LocalApi
from hue_agent.journal import Journal
from test_core import FakeIo


class OptionalCloudTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_cloud_config_does_not_kill_local_api(self):
        module_name='hue_agent._main_under_test'
        spec=importlib.util.spec_from_file_location(module_name,Path(__file__).resolve().parents[1]/'hue_agent/__main__.py')
        main=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'fcntl':ModuleType('fcntl')}):spec.loader.exec_module(main)
        for cloud in (None, [], 'invalid', True):
            with self.subTest(cloud=cloud), tempfile.TemporaryDirectory() as folder:
                root=Path(folder);token='test-local-token-'+'x'*40
                (root/'api.token').write_text(token)
                io=FakeIo();io.disconnected=False
                async def connect():io.disconnected=False
                async def close():pass
                io.connect=connect;io.close=close
                api=[];journal=[];callbacks=[]
                def make_api(*args):
                    obj=LocalApi(*args);api.append(obj);return obj
                def make_journal(*args):
                    obj=Journal(*args);journal.append(obj);return obj
                loop=asyncio.get_running_loop()
                with patch.object(main,'private_file',side_effect=Path), patch.object(main,'BleIo',return_value=io), patch.object(main,'LocalApi',side_effect=make_api), patch.object(main,'Journal',side_effect=make_journal), patch.object(loop,'add_signal_handler',side_effect=lambda sig,callback:callbacks.append(callback)):
                    task=asyncio.create_task(main.run({'journal':str(root/'journal.sqlite'),'token_file':str(root/'api.token'),'port':0,'cloud':cloud}))
                    try:
                        async with asyncio.timeout(1):
                            while not api or api[0].server is None:await asyncio.sleep(0.001)
                        await asyncio.sleep(0.02)
                        self.assertFalse(task.done(),'optional cloud value terminated local agent')
                        port=api[0].server.sockets[0].getsockname()[1]
                        reader,writer=await asyncio.open_connection('127.0.0.1',port)
                        writer.write(f'GET /v1/state HTTP/1.1\r\nAuthorization: Bearer {token}\r\n\r\n'.encode());await writer.drain()
                        response=await reader.read();writer.close();await writer.wait_closed()
                        self.assertIn(b'200 OK',response)
                        self.assertEqual(json.loads(response.split(b'\r\n\r\n')[1])['status'],'verified')
                    finally:
                        for callback in callbacks:callback()
                        await asyncio.gather(task,return_exceptions=True)
                        for instance in api:await instance.close()
                        for instance in journal:instance.close()
