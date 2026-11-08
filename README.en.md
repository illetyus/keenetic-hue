# Keenetic Hue

![Keenetic Hue — Hue BLE, Entware and Google Home integration](docs/assets/hero.png)

**Control a Philips Hue BLE lamp from Google Home through your Keenetic router—without a Hue Bridge and without opening inbound ports.**

[Türkçe ana sayfa](README.md) · [Installation](docs/installation.md) · [Architecture](docs/architecture.md) · [Security](SECURITY.md) · [RC12 Release Notes](docs/releases/0.2.0-rc.12.md) · [Repository Design Research](docs/repository-design.md)

> **Status (`v0.2.0-rc.12`):** Release candidate that passed live command execution, HomeGraph state reporting, and router reboot acceptance on the documented personal hardware profile. This is a personal systems integration—not a Google-certified commercial product or a universal Hue Bridge replacement. Different hardware and fresh Google/Cloudflare accounts require independent validation.

---

## Why Keenetic Hue?

Controlling a single Bluetooth Low Energy (BLE) Hue bulb remotely via Google Home typically forces a choice between buying a dedicated Zigbee bridge or exposing an always-on home server. **Keenetic Hue** runs directly on an always-on Keenetic router's USB/Entware environment with strict operational and privilege boundaries:

| Engineering Dimension | Typical DIY / Bridge Setup | Keenetic Hue Approach |
| --- | --- | --- |
| **Network Surface** | Inbound port forwarding or full-network VPN | **Zero inbound ports:** Router-initiated outbound Cloudflare Tunnel + scoped Cloudflare Worker |
| **Credential Isolation** | Cloud tokens and BLE keys co-located in one runtime | **Strict trust split:** OAuth & HomeGraph stay in the Worker; BLE bond keys and local API tokens (`0600`) never leave the router |
| **Privilege Separation** | Single monolithic daemon running as `root` | **Dedicated principals:** BLE agent runs as `hue-agent` (`61001`), tunnel as `hue-tunnel` (`61000`), verified via `SO_PEERCRED`, `boot_id`, and `startTicks` |
| **Physical Command Safety** | Blind retries on network timeouts (causing unintended bulb toggling) | **No blind replay:** Commands unconfirmed by a physical GATT read are recorded as `OUTCOME_UNKNOWN` and never auto-replayed |
| **Crash Recovery** | Opening or deleting corrupted SQLite journals directly as `root` | **Staged opaque recovery:** Hot `commands.sqlite` + journal pairs are preserved under `0700`; recovery runs on a staged copy under the unprivileged `hue-agent` identity |
| **Updates & Rollback** | Manual `scp` file overwrites | **Ed25519-signed `.huerel` bundles:** Atomic slot switching (`active.json`), live health gates, and automatic `rollback` on failure |

---

## Architecture & Trust Boundaries

![Keenetic Hue architecture](docs/assets/architecture.svg)

A Cloudflare Worker handles personal Google Home OAuth linking and Smart Home intents (`SYNC`, `QUERY`, `EXECUTE`), communicating with the router over an outbound-only Cloudflare Tunnel. On the router, `hue_admin` supervises isolated `hue-tunnel` and `hue-agent` processes while `hue_agent` drives the USB Bluetooth HCI adapter via Google Bumble. Detailed threat and boundary model: **[Architecture](docs/architecture.md)**.

---

## Feature – Module – Test Traceability

| Capability / Security Boundary | Source Module | Verification Test |
| --- | --- | --- |
| **BLE GATT control & physical readback verification (`verified` / `OUTCOME_UNKNOWN`)** | [`hue_agent/agent.py`](hue_agent/agent.py), [`hue_agent/lamp.py`](hue_agent/lamp.py), [`hue_agent/bumble_io.py`](hue_agent/bumble_io.py) | [`tests/test_agent.py`](tests/test_agent.py), [`tests/test_core.py`](tests/test_core.py) |
| **Bounded staged SQLite recovery (retaining original DB/journal pair)** | [`hue_admin/agent_storage.py`](hue_admin/agent_storage.py), [`hue_admin/agent_runtime.py`](hue_admin/agent_runtime.py) | [`tests/test_sqlite_crash_recovery.py`](tests/test_sqlite_crash_recovery.py), [`tests/test_agent_recovery.py`](tests/test_agent_recovery.py) |
| **Dedicated service identities (`hue-agent`, `hue-tunnel`) & process ownership (`boot_id` + `startTicks`)** | [`hue_admin/privileges.py`](hue_admin/privileges.py), [`hue_admin/service_manager.py`](hue_admin/service_manager.py), [`hue_admin/child_identity.py`](hue_admin/child_identity.py) | [`tests/test_privilege_boundary.py`](tests/test_privilege_boundary.py), [`tests/test_agent_process_identity.py`](tests/test_agent_process_identity.py) |
| **Unified health probe (`SO_PEERCRED` Unix socket & component ownership)** | [`hue_admin/unified_health.py`](hue_admin/unified_health.py), [`hue_admin/health.py`](hue_admin/health.py) | [`tests/test_unified_health.py`](tests/test_unified_health.py), [`tests/test_health_peer.py`](tests/test_health_peer.py) |
| **Ed25519-signed `.huerel` packaging, bootstrap & atomic rollback** | [`deploy/build_release.py`](deploy/build_release.py), [`deploy/install_release.py`](deploy/install_release.py), [`hue_admin/releases.py`](hue_admin/releases.py) | [`tests/test_build_release.py`](tests/test_build_release.py), [`tests/test_update.py`](tests/test_update.py) |
| **Encrypted password-protected backups (`.huebak`) & transactional restore** | [`hue_admin/backup.py`](hue_admin/backup.py), [`hue_admin/restore.py`](hue_admin/restore.py), [`hue_admin/restore_transaction.py`](hue_admin/restore_transaction.py) | [`tests/test_backup_restore.py`](tests/test_backup_restore.py), [`tests/test_restore_transaction.py`](tests/test_restore_transaction.py) |
| **Google Home Smart Home intents, OAuth 2.0 & HomeGraph reporting** | [`cloud/src/index.ts`](cloud/src/index.ts), [`cloud/src/google-home.ts`](cloud/src/google-home.ts), [`cloud/src/homegraph.ts`](cloud/src/homegraph.ts) | [`cloud/test/google-home.test.ts`](cloud/test/google-home.test.ts), [`cloud/test/homegraph.test.ts`](cloud/test/homegraph.test.ts) |

---

## Verified Hardware & Runtime Profile

| Component | Accepted Configuration |
| --- | --- |
| **Lamp** | Philips Hue White **LWA017** (BLE, on/off and white-light brightness) |
| **Bluetooth Adapter** | Belkin **BCM20702A0** USB HCI dongle |
| **Router Environment** | Keenetic with Entware on ext4 USB storage (`mips-3.4`) |
| **Runtime** | CPython **3.13** with exact hashes pinned in [`requirements-router.lock.json`](requirements-router.lock.json) |
| **Cloud Layer** | Personal Cloudflare Worker + Tunnel and Google Home developer project (`[test] Keenetic Hue`) |

---

## Three-Stage Setup & Verification

1. **Pre-installation:** Prepare ext4 USB storage, Entware, and pinned dependencies ([prerequisites](docs/reinstall.md)). Verify the Ed25519 public key fingerprint (`3eb6abbce938c6f75054da02ecca3cb291f576ef0633b5de54dd78fa2cd8a4be`) for [`keys/release-trust.pub`](keys/release-trust.pub).
2. **Installation & Restore:** Follow [signed installation](docs/installation.md), configure [cloud resources](docs/cloud-rebuild.md) and [Google Home linking](docs/google-home.md), or restore an existing setup from an encrypted `.huebak` archive via [backup & restore](docs/backup-restore.md). *(Detailed operational guides are written in Turkish; [`config.example.json`](config.example.json) contains placeholder values only.)*
3. **Post-installation Verification:** Run the read-only health check on the router:

```sh
huectl status
huectl health --live --json
```

Expected sanitized output when both service principals, the local API, the BLE link, and the outbound Tunnel are healthy:

```json
{
  "schemaVersion": 1,
  "healthy": true,
  "releaseId": "0.2.0-rc.12",
  "components": {
    "agent":     { "state": "running", "reason": "OWNED_VERSION" },
    "tunnel":    { "state": "ready",   "reason": "TUNNEL_READY" },
    "manager":   { "state": "running", "reason": "BOTH_COMPONENTS_OWNED" },
    "localApi":  { "state": "ready",   "reason": "API_RESPONSE" },
    "ble":       { "state": "ready",   "reason": "VERIFIED" },
    "reporting": { "state": "unknown", "reason": "NOT_CHECKED" }
  },
  "principals": {
    "agent":  { "expectedUid": 61001, "expectedGid": 61001 },
    "tunnel": { "expectedUid": 61000, "expectedGid": 61000 }
  }
}
```

---

## Local Verification, Governance & License

Run portable source checks and unit tests locally:

```sh
python tools/check_publication.py
python tools/run_portable_tests.py
cd cloud && npm run typecheck && npm test
```

[Architecture](docs/architecture.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Support](SUPPORT.md) · [Repository Design Research](docs/repository-design.md)

Project code is [MIT licensed](LICENSE). Third-party components retain their own [licenses](THIRD_PARTY_NOTICES.md). Product names belong to their respective owners; no affiliation or endorsement is implied.
