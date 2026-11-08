import tempfile
import unittest
from pathlib import Path
from hue_admin.service_manager import private_bytes


class OwnerIoTests(unittest.TestCase):
    def test_binary_key_bytes_are_not_text_translated(self):
        raw=bytes(range(256))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fixture.pub';path.write_bytes(raw);path.chmod(0o600)
            self.assertEqual(private_bytes(path),raw)
