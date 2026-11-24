# Changelog

## 0.2.0-rc.24 — 2026-11-24

Code commit `cc5daf8`. Eliminated USB flash event-loop and command-path stalls
across `hue_agent/journal.py`, `hue_agent/agent.py`, `hue_agent/bumble_io.py`,
and `hue_agent/tunnel_api.py`, plus resilient `/v1/session` handshakes and
pre-delivery queue retries in `cloud/src/relay.ts`, `cloud/src/protocol.ts`,
`cloud/src/command-queue.ts`, and `cloud/src/oauth-store.ts`, verified with 57
targeted agent/journal/crash-recovery tests (`2` POSIX root fixtures skipped on
Windows), 96 portable Python tests, 15 Cloudflare Worker test files (76
`vitest` tests + `tsc --noEmit`), `tools/check_publication.py`, and live router
acceptance on `keenetic-hue-0.2.0-rc.24.huerel` (`healthy: true`,
`bootstrapInSync: true`, `releaseId: "0.2.0-rc.24"`, `5/5` cloud queries
`625–890 ms`, `3/3` consecutive `set` commands `1,282–1,313 ms`).
[Release notes](docs/releases/0.2.0-rc.24.md).

- Moved read-only `query` deduplication, sequence, and conflict tracking into
  memory and switched `commands.sqlite` to `PRAGMA journal_mode=PERSIST` +
  `PRAGMA synchronous=FULL` with a dedicated single-worker executor
  (`hue_agent/journal.py`, `hue_agent/agent.py`), eliminating multi-second
  `asyncio` event-loop freezes on slow USB flash.
- Performed fast RAM-first `set` reservation before BLE writes while persisting
  `commands.sqlite` reservations and completions in FIFO order on the
  single-worker background thread, cutting live cloud `set` latency from
  `6,172 ms` to `1,282–1,313 ms`.
- Added sanitized `ble_disconnected` reason logging and fast BLE-only reconnect
  on cleanly disconnected `BleIo` sessions with automatic fallback to full USB
  transport reopen on error (`hue_agent/bumble_io.py`).
- Relaxed `/v1/session` RTT ceiling to `2.5 s` with one retry, extended `set`
  command TTL to `7 s` (`8 s` Worker timeout, `9 s` `TunnelController` ceiling),
  and added one pre-delivery `CLOCK_UNCERTAIN`/`BLE_OFFLINE` retry (skipped if
  superseded by a newer brightness command; `OUTCOME_UNKNOWN` never retried) in
  `cloud/src/relay.ts`, `cloud/src/protocol.ts`, `cloud/src/command-queue.ts`,
  and `cloud/src/oauth-store.ts`.

## 0.2.0-rc.21 — 2026-11-24

Code commit `9ee27ce`. Hue BLE brightness transition improvements across
`hue_agent/lamp.py` and `hue_agent/agent.py`, plus Google Home
`BrightnessRelative` support and post-`OUTCOME_UNKNOWN` state sync in
`cloud/src/google-home.ts` and `cloud/src/oauth-store.ts`, verified with 21
agent tests, 94 portable Python tests, 73 Cloudflare Worker `vitest` tests +
`tsc --noEmit`, `tools/check_publication.py`, and live router acceptance on
`keenetic-hue-0.2.0-rc.21.huerel` (`healthy: true`, `bootstrapInSync: true`,
`releaseId: "0.2.0-rc.21"`). [Release notes](docs/releases/0.2.0-rc.21.md).

- Skipped redundant `POWER` writes when changing brightness on an already-on bulb,
  skipped no-op state writes, and added single-retry brightness recovery on
  `OFF` → `ON` transitions in `hue_agent/lamp.py` (`tests/test_core.py`).
- Allowed `Controller.execute()` in `hue_agent/agent.py` to wait briefly while
  `monitor()` is actively reconnecting and preserved healthy BLE links on
  transient `STATE_CHANGED_DURING_COMMAND` errors (`tests/test_agent.py`).
- Added `BrightnessRelative` (`brightnessRelativePercent` /
  `brightnessRelativeWeight`) support and `OnOff(true) + BrightnessAbsolute`
  batch collapsing in `cloud/src/google-home.ts`, plus read-only state sync
  after `OUTCOME_UNKNOWN` in `cloud/src/oauth-store.ts`
  (`cloud/test/google-home.test.ts`, `cloud/test/queued-execution.test.ts`).

## 0.2.0-rc.20 — 2026-11-23

Code commit `bd0a799`. RAM operation directory and verified tunnel readiness
probe hardening across `hue_admin/storage_operation.py`, `hue_admin/health.py`,
and `hue_admin/unified_health.py`, verified with 26 targeted tests (`2` POSIX
root fixtures skipped on Windows), 91 portable Python tests, 574 discovered
tests (`433` passing, `141` Windows-skipped native Linux/POSIX/root checks),
`tools/check_publication.py`, and live router acceptance on
`keenetic-hue-0.2.0-rc.20.huerel` (`healthy: true`, `bootstrapInSync: true`,
`releaseId: "0.2.0-rc.20"`). [Release notes](docs/releases/0.2.0-rc.20.md).

- Replaced the fixed `/tmp/hue-storage-operations` base directory in
  `hue_admin/storage_operation.py` `start()` and `dispatch()` with randomized
  `0o700` root-only directories via `tempfile.mkdtemp(prefix='hue-storage-operations-', dir=_tmp_base())`
  and `O_DIRECTORY | O_NOFOLLOW` lookup (`_find_operation_directory()`),
  preserving `/tmp` (`tmpfs`) independence from `/opt` while preventing local
  pre-creation DoS (`tests/test_storage_operation.py`).
- Added `verified_probe()` (sharing `_verified_probe()`) in
  `hue_admin/health.py` and switched the `127.0.0.1:18790/ready` probe in
  `hue_admin/unified_health.py` and legacy `health.py` to enforce
  `verify_tcp_peer()` socket matching, `verify_process_identity()` UID/GID
  checks, and fail-closed `peer_guard` validation before sending HTTP `GET`
  (`tests/test_health_peer.py`, `tests/test_unified_health.py`).

## 0.2.0-rc.19 — 2026-11-14

Code commit `ececa49`. SQLite rollback sidecar (`-wal`, `-shm`, `-journal`)
cleanup hardening across `hue_admin/restore_transaction.py`,
`hue_admin/migration.py`, and `hue_admin/initialize_restore.py`, verified with
20 targeted regression tests, 91 portable Python tests (569 passing in `tests/`
with 141 Windows-skipped native Linux/POSIX/root checks),
`tools/check_publication.py`, and live router acceptance on
`keenetic-hue-0.2.0-rc.19.huerel` (`healthy: true`, `bootstrapInSync: true`,
`releaseId: "0.2.0-rc.19"`). [Release notes](docs/releases/0.2.0-rc.19.md).

- Removed stale `-wal`, `-shm`, and `-journal` sidecars before restoring
  `state/commands.sqlite` in `hue_admin/restore_transaction.py` `recover()`, and
  added regression coverage in `tests/test_restore_transaction.py`.
- Extended `hue_admin/migration.py` `rollback()` sidecar cleanup to remove
  `commands.sqlite-journal` in legacy `agent/state` and `('-wal', '-shm',
  '-journal')` under `root/state/`, with regression coverage in
  `tests/test_existing_migration.py`.
- Quarantined `state/commands.sqlite{-wal,-shm,-journal}` during
  `recover_if_needed()` and rejected stale sidecars in `initialize()` in
  `hue_admin/initialize_restore.py`, with regression coverage in
  `tests/test_initialize_restore.py`.

## 0.2.0-rc.18 — 2026-11-14

Code commit `674b153`. Security hardening across `hue_admin/cli.py`,
`deploy/install.py`, and `deploy/update.py`, verified with 91 portable Python
tests (out of 566 total Python tests including 141 Windows-skipped native
Linux/POSIX/root checks), `tools/check_publication.py`, and live router
acceptance on `keenetic-hue-0.2.0-rc.18.huerel` (`healthy: true`,
`bootstrapInSync: true`, `releaseId: "0.2.0-rc.18"`).
[Release notes](docs/releases/0.2.0-rc.18.md).

- Removed the undocumented `?` branch in `hue_admin/cli.py` that printed an
  auto-generated backup password to `sys.stderr`, enforcing non-echoing `getpass`
  confirmation and adding regression coverage in `tests/test_admin_cli.py`.
- Enforced explicit `0o600` descriptor writes (`os.open` + `O_NOFOLLOW` +
  `fchmod`) and cleaned up legacy taint naming in `deploy/install.py` and
  `deploy/update.py`.

## 0.2.0-rc.17 — 2026-11-14

Code commit `2c0df72`. Operational hygiene and hardening across `hue_agent/`,
`deploy/`, and `hue_admin/`, verified with 91 portable Python tests (out of 566
total Python tests including 141 Windows-skipped native Linux/POSIX/root checks),
`tools/check_publication.py`, and live router acceptance on
`keenetic-hue-0.2.0-rc.17.huerel` (`healthy: true`, `bootstrapInSync: true`,
`releaseId: "0.2.0-rc.17"`). [Release notes](docs/releases/0.2.0-rc.17.md).

- Coalesced repeated identical `ble_retry` warnings in `hue_agent/agent.py`
  after attempt 6 (logging on error-type change or every 80 attempts) and
  included `recoveredAfter` on `ble_ready`.
- Redirected `fd 1` (`stdout`) and `fd 2` (`stderr`) to `/dev/null` before
  `execve` in `deploy/component-child.py`'s `tunnel` branch while restoring
  `stderr` on `execve` failure.
- Added post-commit `prune_obsolete_releases` in `hue_admin/activation.py` and
  `prune_bootstrap_backups(root, keep=2)` in `hue_admin/bootstrap_install.py`
  with regression tests in `tests/`.

## 0.2.0-rc.16 — 2026-11-11

Code commit `311c46e`. Hardening across `hue_agent/`, `hue_admin/`, `cloud/`,
`deploy/`, and `tools/`, locally verified with 91 portable Python tests (out of
562 total Python tests including native Linux/POSIX/root checks), 70 Cloudflare
Worker Vitest tests, TypeScript checking, and live router acceptance on
`keenetic-hue-0.2.0-rc.16.huerel` (`healthy: true`, `bootstrapInSync: true`,
`releaseId: "0.2.0-rc.16"`). Live Cloudflare Worker deployment requires separate
`wrangler login` and `wrangler.personal.jsonc` deployment.
[Release notes](docs/releases/0.2.0-rc.16.md).

- Help/empty CLI invocation bypasses the management lock cleanly in `admission.py`
  and `bootstrap.py`; `deploy/huectl-entry.py` reports `KeyboardInterrupt` cleanly
  with exit code `130`.
- `bootstrap_sync_status` and `huectl update` report `bootstrapInSync`,
  `bootstrapSyncRequired`, and `bootstrapReleaseId` while preserving `root/manager`
  isolation; `provision_bootstrap` supports committed prior-release/backup entry
  shim upgrades and cleans up leftover `.bootstrap-stage-*` directories.
- Hardened management lock scoping, symlink/mode/ownership checks, backup token
  bounds, and recovery archive pruning across `initialize_restore.py`,
  `agent_storage.py`, `configure_issuer.py`, `restore_current.py`,
  `runtime_repair.py`, `service_manager.py`, and `archive_commands.py`; added
  `GOOGLE_QUEUE_ENABLED='true'` documentation alignment for Cloudflare Worker
  `EXECUTE` multi-step handling, supervisor `flock`/FD/isolated-python guards in
  `deploy/`, and local HTTP/BLE/Worker hardening.

## 0.2.0-rc.15 — 2026-11-10

Commit `2c61cc1`. Ten CLI, activation, and administration hardening fixes across
`hue_admin/` (`activation.py`, `admission.py`, `cli.py`, `installation.py`,
`lifecycle_events.py`, `owned_command.py`, `restore.py`, `restore_current.py`, and
`service_manager.py`). Live router `huectl status --json` and
`huectl health --live --json` passed (`releaseId: "0.2.0-rc.15"`, `healthy: true`).
[Release notes](docs/releases/0.2.0-rc.15.md).

## 0.2.0-rc.14 — 2026-11-10

Commit `b90a923`. Publication secret filtering in `tools/check_publication.py`,
non-POSIX `O_NOFOLLOW` compatibility guards in `hue_admin/`, and `.gitignore`
patterns.

## 0.2.0-rc.13 — 2026-11-09

Commit `1370c22`. Deterministic release metadata in `deploy/build_release.py` and
`hue_admin/releases.py`, explicit `PACKAGE_MODULES` allowlist in
`deploy/build_release.py`, private token path checks in `hue_agent/cloud_link.py`,
and `.github/dependabot.yml` pip tracking.

## 0.2.0-rc.12 — 2026-11-08

Preserved-pair SQLite recovery under a dedicated identity; crash-safe checkpoint
and reseed; bounded lifecycle diagnostics. Live command, automatic reboot startup
and matching HomeGraph state accepted. [Release notes](docs/releases/0.2.0-rc.12.md).

## Source publication preparation

Local SSH settings for the optional RAM helper, original architecture artwork,
Turkish/English onboarding, security/contribution/support and third-party notices.
These changes do not alter the accepted signed RC12 payload.
