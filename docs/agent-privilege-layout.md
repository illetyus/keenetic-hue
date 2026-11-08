# Privilege Separation and Runtime Data Layout for `hue-agent`

This design note records the privilege-separation architecture and hardware
validation trials introduced in RC5–RC7 and carried forward into RC12. For daily
RC12 operator commands, see the [daily maintenance quickstart](maintenance-quickstart.md)
and [installation guide](installation.md).

## Verified Hardware Privilege Isolation

- The USB Bluetooth device node (`050d:065a`, Belkin BCM20702A0) is scoped to
  `root:61001` (`0660`). The `hue-agent` process runs under dedicated UID/GID
  `61001` with empty supplementary groups, zero Linux capabilities, and
  `no_new_privs=1`, while `hue-tunnel` runs under UID/GID `61000`.
- Python is launched with `-s -P -u -m` so user `site-packages` and current-working-directory
  imports are disabled; only the verified release `PYTHONPATH` is used.

## Runtime Storage Rulings

- **Ruling — Root Master vs. Agent Runtime Projection:** The unified root
  (`/opt/var/lib/keenetic-hue-system`) remains `0700` owned by `root`. Rather than
  relaxing permissions on the unified root, `agent_storage.agent_runtime_paths()`
  projects a dedicated runtime tree with `root`-pinned parent directories:

| Path | Purpose |
| --- | --- |
| `...-runtime/agent/code/<version>` | Verified Python payload from the signed manifest (`root`-writable only) |
| `...-runtime/agent/config` | `root`-generated configuration and the two scoped API tokens readable only by the agent group |
| `...-runtime/agent/state` | Agent-owned BLE bond (`bond.json`) and command journal (`commands.sqlite`) |
| `...-runtime/agent/run` | Agent PID file, USB lock, and private Unix health socket (`health.sock`) |
| `...-runtime/agent/logs` | Size-bounded rotating agent logs |

- **Ruling — Safe File Descriptor Inspection (`read_agent_private`):** Whenever
  `root` reads agent-written files, it validates owner, group, private mode (`0600`),
  regular file type, single hardlink count (`st_nlink == 1`), and maximum size on
  the opened file descriptor (`fstat`) before reading bytes.
- **Ruling — Never Parse Agent SQLite as `root`:** `snapshot_agent_database()` never
  opens agent-controlled SQLite files inside the `root` process. All SQLite
  validation and checkpointing run inside a resource-bounded child worker under
  UID/GID `61001` (output capped at 16 MiB, CPU 12s, address space 64 MiB, wall
  clock 20s), inheriting only an empty output file descriptor opened by `root`.
- **Ruling — Peer-Credential Health Probes:** `authenticated_probe()` verifies the
  Unix domain socket owner/mode, `SO_PEERCRED` PID/UID/GID, kernel `boot_id`, and
  process `startTicks` against the signed release policy before sending any local
  API bearer token.
- **Ruling — Scoped USB Hotplug (`/opt/etc/ndm/usb.d`):** Hotplug events act only as
  triggers. `usb_policy.target_for_usb()` rescans fixed `sysfs` paths and verifies
  VID/PID, exact serial number, character device type, `rdev`, and inode before
  granting group `61001` access to the single matching USB node.
