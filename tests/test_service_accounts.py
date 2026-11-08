import unittest

from hue_admin.service_accounts import plan_accounts, provision_accounts


class ServiceAccountTests(unittest.TestCase):
    def tables(self):
        return {'native_passwd': 'root:x:0:0:root:/root:/bin/sh\n',
                'native_group': 'root:x:0:\n',
                'entware_passwd': 'root:x:0:0:root:/root:/bin/sh\n',
                'entware_group': 'root:x:0:\n'}

    def test_separate_unused_ids_and_preserve_existing_rows(self):
        tables = self.tables()
        tables['native_passwd'] += 'other:x:61000:61000::/:/bin/sh\n'
        result, identities = plan_accounts(tables)
        self.assertEqual(identities['hue-tunnel'], {'schema': 1, 'uid': 61001, 'gid': 61001})
        self.assertEqual(identities['hue-agent'], {'schema': 1, 'uid': 61002, 'gid': 61002})
        for name, original in tables.items():
            self.assertTrue(result[name].startswith(original))
        self.assertIn('hue-tunnel:*:61001:61001:', result['native_passwd'])

    def test_idempotent_and_rebuilds_missing_native_rows(self):
        result, identities = plan_accounts(self.tables())
        self.assertEqual(plan_accounts(result, identities), (result, identities))
        rebooted = dict(result, native_passwd=self.tables()['native_passwd'],
                        native_group=self.tables()['native_group'])
        self.assertEqual(plan_accounts(rebooted, identities), (result, identities))

    def test_profile_collision_never_reassigns_identity(self):
        result, identities = plan_accounts(self.tables())
        result['native_passwd'] += 'intruder:x:61000:65000::/:/bin/sh\n'
        with self.assertRaises(ValueError):
            plan_accounts(result, identities)

    def test_named_account_with_login_shell_is_rejected(self):
        tables = self.tables()
        tables['entware_passwd'] += 'hue-tunnel:x:61000:61000::/:/bin/sh\n'
        with self.assertRaises(ValueError):
            plan_accounts(tables)

    def test_shared_group_members_and_duplicate_names_rejected(self):
        result, identities = plan_accounts(self.tables())
        for suffix in ('hue-tunnel:*:61000:someone\n', 'hue-tunnel:*:62000:\n'):
            broken = dict(result, native_group=result['native_group'] + suffix)
            with self.assertRaises(ValueError):
                plan_accounts(broken, identities)

    def test_malformed_or_root_profile_rejected(self):
        for value in ({}, {'hue-tunnel': {'schema': 1, 'uid': 0, 'gid': 0}},
                      {'hue-tunnel': {'schema': 1, 'uid': True, 'gid': 61000}}):
            with self.assertRaises(ValueError):
                plan_accounts(self.tables(), value)

    def test_unterminated_existing_table_fails_without_rewriting(self):
        tables = self.tables()
        tables['native_passwd'] = tables['native_passwd'].rstrip('\n')
        with self.assertRaises(ValueError):
            plan_accounts(tables)

    def test_profiles_are_durable_before_any_account_write(self):
        events = []
        profiles = provision_accounts(self.tables(), None,
            lambda value: events.append(('profiles', value)),
            lambda key, old, suffix: events.append(('append', key, old, suffix)))
        self.assertEqual(events[0], ('profiles', profiles))
        self.assertEqual(len(events), 5)
        for event in events[1:]:
            self.assertEqual(event[2], self.tables()[event[1]])
            self.assertTrue(event[3].startswith('hue-tunnel:*:'))

    def test_partial_write_failure_can_be_repaired_with_saved_profile(self):
        tables = self.tables()
        saved = []
        def append(key, old, suffix):
            if key == 'entware_passwd':
                raise OSError('fixture power loss')
            self.assertEqual(tables[key], old)
            tables[key] += suffix
        with self.assertRaises(OSError):
            provision_accounts(tables, None, saved.append, append)
        self.assertEqual(len(saved), 1)
        def finish(key, old, suffix):
            self.assertEqual(tables[key], old)
            tables[key] += suffix
        provision_accounts(tables, saved[0], lambda _: self.fail('profile rewritten'), finish)
        expected, _ = plan_accounts(self.tables(), saved[0])
        self.assertEqual(tables, expected)
