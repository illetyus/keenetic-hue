import json
import time
import unittest
from pathlib import Path
from hue_agent.protocol import Command, ProtocolError


class CloudFixtureTests(unittest.TestCase):
    def test_shared_fixture_and_local_int64(self):
        fixture = json.loads((Path(__file__).parent/'fixtures/cloud-v1.json').read_text())
        base = dict(commandId='fixture', deviceId='hue-lwa017-01', sessionId='fixture',
                    sequence=1, expiresAt=time.time()+5)
        for case in fixture['valid']:
            expected = case.get('expectedRaw')
            fields = {k:v for k,v in case.items() if k != 'expectedRaw'}
            cmd = Command.parse(dict(base, **fields), 'fixture')
            if 'expectedRaw' in case:
                self.assertEqual(cmd.brightness_raw, expected)
        for case in fixture['invalid']:
            with self.subTest(case=case), self.assertRaises(ProtocolError):
                Command.parse(dict(base, **case), 'fixture')
        self.assertEqual(Command.parse(dict(base, operation='query', sequence=2**53), 'fixture').sequence, 2**53)
