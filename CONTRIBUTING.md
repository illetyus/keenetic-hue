# Contributing

Read the [architecture](docs/architecture.md), [runtime lock](requirements-router.lock.json)
and [security policy](SECURITY.md). Keep changes focused and explain why they
are needed and what was actually tested. Use respectful, evidence-based discussion.

## Development checks

In a disposable Python 3.12/3.13 environment:

```sh
python -m pip install -r requirements-pure-router.txt cryptography==46.0.5
python -m pip install --no-deps bumble==0.0.236
python tools/check_publication.py
python tools/run_portable_tests.py
```

These are development commands, not Entware installation commands. For the Worker:

```sh
cd cloud
npm ci
npm run typecheck
npm test
```

Use dummy configuration and test secrets. Do not deploy tests to a personal
live Worker. Native UID/capability, storage and reboot acceptance are separate
from portable tests and require an isolated authorized environment.

## Review expectations

- Preserve deadlines, deduplication and `OUTCOME_UNKNOWN` semantics.
- Root must not parse agent-controlled SQLite; preserve DB/journal evidence
  before bounded staged recovery under the dedicated identity.
- Do not weaken signatures, host-key checks, ownership or compatibility gates.
- Do not add personal addresses, key paths, credentials or backups to source/history.
- Use disposable fixtures; no USB/BLE device access without explicit authorization.
- Update user-facing guides when behavior changes. A unit test is not a physical
  lamp or fresh-account cloud acceptance.

Project contributions must be your own work or preserve compatible third-party
attribution. Original project code uses the [MIT license](LICENSE).
