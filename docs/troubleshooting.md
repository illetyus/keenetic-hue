# Troubleshooting

These procedures cover the unified Keenetic Hue installation and the RC12
maintenance workflow.

Run commands over SSH in an interactive Entware terminal. When sharing diagnostic
output, never include setup codes, passphrases, tokens, BLE bonds, or `.huebak`
backups. Never factory-reset the Hue bulb, delete BLE pairing keys, or format a
working Entware USB drive during routine troubleshooting.

## SSH Host Key Changed Warning

If you see `REMOTE HOST IDENTIFICATION HAS CHANGED`, stop immediately. While a host
key change can follow a genuine router or Entware reinstallation, it can also
indicate connecting to the wrong device or a man-in-the-middle risk. Never treat the
fingerprint shown inside the SSH warning itself as verification.

Compare the fingerprint against a previously recorded trusted value or verify it
through a trusted local Keenetic administration session independent of SSH. Only
update `known_hosts` once the change is independently confirmed for that exact
router and port. Never disable `StrictHostKeyChecking` or rely on unverified
`ssh-keyscan` output.

## 1. Check the Entware USB Mount and `huectl`

Ensure the Entware USB drive is plugged into the Keenetic router and mounted at
`/opt`:

```sh
df -h /opt
test -x /opt/bin/huectl && echo HUECTL_OK
```

If `HUECTL_OK` is not printed, `/opt` may not be mounted. Check physical USB
attachment and Keenetic's external storage/OPKG status page. Do not re-run bootstrap
installation commands over an unmounted `/opt`. For actual USB hardware loss, follow
the [reinstallation guide](reinstall.md).

## 2. Isolate Agent, BLE, and Tunnel State

```sh
huectl status
huectl health --live --json
```

`status` checks whether owned service processes are running; `health --live --json`
queries the local API, performs a live read-only BLE GATT check against the bulb,
and checks Tunnel readiness without toggling the lamp. A healthy check returns
`healthy: true` and exit code `0` (`1` indicates degraded/stopped state; `2`
indicates invalid CLI usage or a missing interactive TTY).

Inspect the `components` object in the JSON output:

- **`manager` or `agent` is `stopped` / `degraded`:** Verify `/opt` is mounted and
  check `huectl status`.
- **`localApi` is `degraded` (`LOCAL_CHECK_FAILED`):** The agent did not respond on
  its local health endpoint. Check USB and `agent` status.
- **`ble` is `degraded` (`BLE_UNAVAILABLE`):** Verify that the physical wall switch
  supplying the Hue bulb is turned on and the USB Bluetooth adapter is seated. Run
  `huectl health --live --json` once more. Never run external BLE scan/pair tools
  while the service owns the adapter.
- **`tunnel` is `degraded` (`TUNNEL_UNREACHABLE`):** Check Keenetic WAN
  connectivity, DNS, and system clock. If the BLE agent is healthy, restart only the
  Tunnel:

  ```sh
  huectl restart tunnel
  huectl health --live --json
  ```

- **`reporting` is `unknown` (`NOT_CHECKED`):** Normal for local health queries,
  which do not contact Google's HomeGraph API. If Google Home shows offline while
  local health is green, inspect the cloud path using the
  [cloud link operations guide](cloud-link-operations.md).

If only `agent` is stopped or BLE readback repeatedly fails after verifying mains
power and USB attachment, restart the agent:

```sh
huectl restart agent
huectl health --live --json
```

If a service action exits with code `1` or prints `SERVICE_ACTION_FAILED`, do not
manually `kill` processes; inspect `huectl status` and `huectl diagnose --json`
(redacting any private paths before sharing).

## 3. SQLite Startup Checkpoint Errors and Lifecycle Logs

If you see:

`SERVICE_ACTION_FAILED component=agent stage=checkpoint reason=AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA`

or `CHECKPOINT:AGENT_DATABASE_SNAPSHOT_FAILED:SCHEMA` in the health output, the
agent stopped during its pre-start database check. This code alone does not prove
database corruption: an interrupted SQLite transaction's hot rollback journal
causes read-only opening to fail with the same error code.

During joint startup (`huectl start` or `huectl restart`), Tunnel startup is
intentionally skipped if agent startup fails, recording
`DEPENDENCY:AGENT_START_FAILED` for the Tunnel. Inspect the underlying agent stage
rather than bypassing the dependency with `huectl start tunnel`.

Read-only diagnostic commands in the Entware SSH terminal:

```sh
huectl status
huectl health --json
tail -n 20 /opt/var/lib/keenetic-hue-system/logs/lifecycle.log
tail -n 20 /opt/var/lib/keenetic-hue-system/logs/agent-supervisor.log
tail -n 20 /opt/var/lib/keenetic-hue-system/logs/tunnel-supervisor.log
```

Manager events are recorded in `logs/lifecycle.log` and supervisor events in
`logs/*-supervisor.log` (each bounded to ~64 KiB with one `.previous` rotation).
Records are root-private (`0600`) and log only component name, `boot_id`,
PID/`startTicks`, release ID, UTC/monotonic timestamps, stage, and allowlisted error
codes—never command payloads, keys, tokens, or raw exception strings.

- `SUPERVISOR_TERM` / `SUPERVISOR_INT` indicates a caught termination signal for the
  matching `boot_id`/PID/`startTicks`/release identity.
- `SUPERVISOR_EXIT_UNKNOWN` indicates that no matching terminal signal record was
  found; the tool never fabricates a `SIGKILL`, power-cut, or USB-fault cause.

When a hot SQLite pair is present, always preserve the database and `-journal` file
together—never manually delete the journal, replace the database file alone, or run
SQLite repair commands as `root`. Supported RC12 recovery preserves the original
pair in root-private storage (`0700`), recovers a staged copy under the unprivileged
`hue-agent` (`61001`) principal, verifies schema and integrity, and never replays
`OUTCOME_UNKNOWN` physical commands. Follow the
[signed update guide](update-rollback.md).

## 4. Setup Code, Backup, and Update Error Codes

- **`SETUP_CODE_REQUIRES_TERMINAL`:** Run `huectl setup-code` directly in an
  interactive SSH TTY without redirecting output.
- **`SETUP_CODE_UNAVAILABLE`:** The setup-code issuer token is missing or the HTTPS
  request to the Worker failed. Check WAN/DNS and issuer configuration
  ([Google Home guide](google-home.md)).
- **`PRIVATE_TERMINAL_REQUIRED`:** Run backup and restore commands directly in an
  interactive SSH terminal; passphrases cannot be piped or passed on the CLI.
- **`BACKUP_OR_RESTORE_FAILED`:** Check the file path, free disk space, passphrase,
  and hardware-profile match ([backup & restore guide](backup-restore.md)). If a
  restore was interrupted, run `huectl restore --recover` followed by
  `huectl health --live --json`.
- **`RELEASE_OPERATION_FAILED` / `RELEASE_OPERATION_INTERRUPTED`:** Check signature,
  compatibility, and maintenance receipts ([update & rollback guide](update-rollback.md)).
  Run `huectl recover` if a package update was interrupted.
