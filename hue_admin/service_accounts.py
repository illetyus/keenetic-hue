"""Plan dedicated service accounts without mutating either account database.

Native account files may be regenerated at boot. Persisted numeric identities
remain authoritative: a conflicting account stops startup instead of silently
moving a service to another UID. The caller owns locking and transactional I/O.
"""

NAMES = ('hue-tunnel', 'hue-agent')
TABLES = ('native_passwd', 'native_group', 'entware_passwd', 'entware_group')


def _rows(text, kind):
    if not isinstance(text, str) or '\x00' in text or (text and not text.endswith('\n')):
        raise ValueError('ACCOUNT_TABLE_FORMAT')
    rows = []
    names = set()
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        parts = line.split(':')
        if (len(parts) != (7 if kind == 'passwd' else 4) or not parts[0]
                or parts[0] in names or not parts[2].isascii() or not parts[2].isdigit()):
            raise ValueError('ACCOUNT_TABLE_ROW')
        if kind == 'passwd' and (not parts[3].isascii() or not parts[3].isdigit()):
            raise ValueError('ACCOUNT_TABLE_ROW')
        names.add(parts[0])
        rows.append(parts)
    return rows


def _entry(name, identity, kind):
    if kind == 'passwd':
        return [name, '*', str(identity['uid']), str(identity['gid']),
                'Keenetic Hue service', '/nonexistent', '/bin/false']
    return [name, '*', str(identity['gid']), '']


def plan_accounts(tables, identities=None):
    """Return full replacement contents plus stable profiles; never write files.

    Exact service rows are deliberately required. Existing unrelated rows are
    preserved byte for byte, including password fields, and never logged.
    """
    if set(tables) != set(TABLES):
        raise ValueError('ACCOUNT_TABLE_SET')
    parsed = {key: _rows(tables[key], key.rsplit('_', 1)[1]) for key in TABLES}
    supplied = identities is not None
    if supplied:
        if not isinstance(identities, dict) or set(identities) != set(NAMES):
            raise ValueError('ACCOUNT_PROFILE_SET')
        identities = {name: dict(value) for name, value in identities.items()}
        for value in identities.values():
            if (set(value) != {'schema', 'uid', 'gid'} or type(value['schema']) is not int
                    or value['schema'] != 1
                    or any(type(value[k]) is not int or not 1 <= value[k] <= 2147483647
                           for k in ('uid', 'gid'))):
                raise ValueError('ACCOUNT_PROFILE')
        if (len({v['uid'] for v in identities.values()}) != len(NAMES)
                or len({v['gid'] for v in identities.values()}) != len(NAMES)):
            raise ValueError('ACCOUNT_SHARED_IDENTITY')
    else:
        identities = {}
        used = {int(row[2]) for rows in parsed.values() for row in rows}
        used.update(int(row[3]) for key, rows in parsed.items()
                    if key.endswith('_passwd') for row in rows)
        # Unprofiled service names could be unrelated/manual accounts. Do not
        # adopt them just because their spelling happens to match.
        if any(row[0] in NAMES for rows in parsed.values() for row in rows):
            raise ValueError('ACCOUNT_UNPROFILED_NAME')
        for name in NAMES:
            number = next((n for n in range(61000, 62000) if n not in used), None)
            if number is None:
                raise ValueError('ACCOUNT_ID_RANGE_EXHAUSTED')
            used.add(number)
            identities[name] = {'schema': 1, 'uid': number, 'gid': number}

    result = dict(tables)
    for key, rows in parsed.items():
        kind = key.rsplit('_', 1)[1]
        for name in NAMES:
            identity = identities[name]
            wanted = _entry(name, identity, kind)
            for row in rows:
                if row[0] == name:
                    if row != wanted:
                        raise ValueError('ACCOUNT_EXISTING_MISMATCH')
                elif int(row[2]) == identity['uid' if kind == 'passwd' else 'gid']:
                    raise ValueError('ACCOUNT_NUMERIC_COLLISION')
                if kind == 'passwd' and row[0] not in NAMES and int(row[3]) == identity['gid']:
                    raise ValueError('ACCOUNT_SHARED_PRIMARY_GROUP')
                if kind == 'group' and name in row[3].split(','):
                    raise ValueError('ACCOUNT_SUPPLEMENTARY_MEMBERSHIP')
            if not any(row[0] == name for row in rows):
                result[key] += ':'.join(wanted) + '\n'
    return result, identities


def provision_accounts(tables, identities, save_profiles, append_table):
    """Execute a checked append plan under the caller's database locks.

    Persist identities first. A crash after any append is repaired using those
    same IDs at the next start; it never causes allocation of a second identity.
    append_table must compare its locked file with `old` before appending.
    """
    replacements, profiles = plan_accounts(tables, identities)
    if identities is None:
        save_profiles(profiles)
    for key in TABLES:
        old = tables[key]
        suffix = replacements[key][len(old):]
        if suffix:
            append_table(key, old, suffix)
    return profiles
