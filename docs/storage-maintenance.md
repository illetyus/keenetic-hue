# Preparing the RAM-Based USB Storage Maintenance Helper

`deploy/storage_ram_prepare.py` is an optional workstation-side helper that stages
Python and verified filesystem check tools into Keenetic RAM (`/tmp`) for advanced
offline USB filesystem maintenance. Normal Hue installation, daily lamp control, and
automatic boot startup do **not** depend on this script, and it is not included in
the signed `.huerel` router bundle.

## 1. Configure Local SSH Connection Settings

Create a git-ignored local settings copy from the repository root:

```powershell
Copy-Item deploy/storage-ram.example.json .storage-ram.local.json
```

Populate `.storage-ram.local.json` with your environment's connection details:

| Field | Description |
| --- | --- |
| `ssh_host` | LAN IP address or hostname of your Keenetic router |
| `ssh_port` | Entware SSH server port as a JSON integer (distinct from the Keenetic OS CLI port) |
| `ssh_user` | Entware SSH username |
| `ssh_key_path` | Path to your local SSH private key file (never the key contents or passphrase) |

On Windows, write paths using forward slashes (`C:/path/to/id_ed25519`). Relative
paths resolve against the settings file directory, and `~` expands to your home
directory. The script never modifies your SSH key file.

The empty `deploy/storage-ram.example.json` file is intentionally non-executable. If
any setting is missing or invalid, the script stops immediately with a
`STORAGE_SETTINGS_...` error before reading payload files or opening an SSH
connection.

Verify the router's SSH host key fingerprint through a trusted channel and add it to
your OpenSSH `known_hosts` beforehand. The helper enforces
`StrictHostKeyChecking=yes`, `IdentitiesOnly=yes`, and `BatchMode=yes`.

`.storage-ram.local.*` files are ignored by Git. Never commit personal IP addresses,
key paths, or maintenance logs to the repository.

## 2. Usage

Requires Python and the OpenSSH client on your workstation, plus key-based Entware
SSH access on the Keenetic router. `TOOL_PAYLOAD.json` must match the pinned IPK
tool digests verified by the script.

To stage the RAM maintenance bundle without starting a filesystem check:

```sh
python deploy/storage_ram_prepare.py TOOL_PAYLOAD.json
```

To specify a custom local settings file or log file:

```sh
python deploy/storage_ram_prepare.py TOOL_PAYLOAD.json --settings LOCAL_SETTINGS.json --log-file .storage-ram.local.log
```

The default `--action prepare` stages the RAM maintenance environment only.
`--action start` initiates the maintenance workflow as a separate explicit step, and
`--action status --operation-id OPERATION_ID` queries operation status. Formatting
utilities (`mke2fs`) are excluded from the maintenance payload.
