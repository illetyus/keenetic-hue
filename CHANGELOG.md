# Changelog

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
