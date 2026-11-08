# Local Hue API and service behavior

For the supported RC12 install and service commands, see [installation](installation.md) and [daily maintenance](maintenance-quickstart.md). The local agent controls the lamp over encrypted BLE. The Cloudflare Worker handles OAuth and HomeGraph reporting separately.

## Local commands

Requests must include `Authorization: Bearer <local token>`; the token is stored in a private file on the router and must not be pasted into chat or shell command lines. `GET /v1/state` always reads the lamp and returns the current process sessionId. `POST /v1/commands` accepts the SPEC command schema: commandId, deviceId, sessionId, sequence, expiresAt (Unix seconds), operation, and optional on/brightness.

Positive brightness sets the raw brightness and turns the lamp on. Brightness 0 turns it off while preserving its remembered raw brightness. On=false with positive brightness and on=true with brightness=0 are rejected as contradictory. Results carry actual observations and never fabricate offline state. Status verified means final reads match all requested fields. External changes after the last read remain possible.

Commands have a maximum 60-second expiry horizon; ordinary callers should use five seconds. Deadlines include time waiting for the one BLE operation lock. New writes are checked against the deadline; an already dispatched radio write cannot be undone by a timeout. An uncertain result is recorded as OUTCOME_UNKNOWN and never automatically replayed. Duplicate IDs with identical payloads return the retained result; conflicting payloads are rejected. The SQLite journal retains at most 256 commands for 30 minutes. A new process has a new sessionId, rejecting delayed commands from old sessions.

The reconnect loop uses bounded timeouts and backoff from one to 30 seconds with jitter. It never invokes pairing/reset. Public controller identity must match the configured controller address; replacing the dongle requires a new deliberate setup. Fresh GET requests provide local state.

The agent owns an advisory lock shared across staging and installed processes, preventing duplicate agents. An unrelated tool that ignores this lock can still contend for the USB device; do not run scan/pair while the service owns it.

## SQLite startup recovery (RC12 candidate)

The recovery-capable candidate requires a reviewed signed release and supported
release deployment. It is not activated merely by building its archive.
Before a stopped agent restarts, the manager validates its existing runtime
database and rollback journal. A genuine hot pair is preserved together as
opaque root-private files before any SQLite recovery. Recovery runs on a staged
copy under the dedicated agent identity, using bounded resources; root does not
parse the database. The recovered snapshot must pass exact schema and integrity
checks before the canonical checkpoint is replaced.

After a successful checkpoint, runtime reseeding archives the previous state
directory before creating a fresh state directory. It never pairs a new main
database with an old rollback journal, manually deletes the original hot journal,
or replays uncertain physical commands. Failed preservation, insufficient space,
unsafe ownership/links, changed inputs, unsupported sidecars or failed validation
stop startup safely. Keep the original evidence and inspect the safe checkpoint
reason in [troubleshooting](troubleshooting.md).

For joint startup, Tunnel is started only after agent startup succeeds. Recovery
success must be followed by live local API/BLE, Tunnel and HomeGraph checks, one
Google Home command, and a router reboot acceptance. Rollback retains the prior
signed code slot; it cannot undo a physical radio action and must not bypass
the preserved-pair or state-validation boundaries. Do not run old checkpoint
code against the original hot pair as a substitute for recovery.

## Sources

- Existing project SPEC.md and 2026-10-23-uygulama-plani.md define contract and acceptance gates.
- [Bumble Device/Peer API](https://github.com/google/bumble/blob/main/bumble/device.py) and [keystore](https://github.com/google/bumble/blob/main/bumble/keys.py) were checked alongside installed 0.0.236 source and actual radio tests.
- Windows reference controller hue_control.py established exact one-byte control semantics. The live Keenetic transition trace established bounded convergence before final comparison.
- [Python 3.13 asyncio streams](https://docs.python.org/3.13/library/asyncio-stream.html) documents coroutine connection callbacks and writer closure. The agent tracks and drains its handler tasks before closing shared state.
