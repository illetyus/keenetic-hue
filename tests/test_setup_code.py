import unittest
from unittest.mock import patch
from hue_admin.setup_code import request_setup_code


class SetupCodeTests(unittest.TestCase):
    def test_only_exact_public_origin_is_accepted(self):
        for origin in ('http://example.com','https://user:secret@example.com','https://example.com/path','https://example.com?token=x'):
            with self.assertRaises(ValueError):
                request_setup_code(origin,'x'*64)

    def test_issuer_never_leaks_into_exception(self):
        secret='sensitive-'+'x'*64
        with patch('hue_admin.setup_code.http.client.HTTPSConnection',side_effect=OSError(secret)):
            with self.assertRaisesRegex(RuntimeError,'SETUP_CODE_UNAVAILABLE') as context:
                request_setup_code('https://relay.test',secret)
        self.assertNotIn(secret,str(context.exception))
