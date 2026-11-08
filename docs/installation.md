# Installation and File Layout

This installation workflow uses the signed RC12 bundle and an independently
verified Ed25519 public key. For Entware preparation and runtime dependencies,
first follow the [preparation steps](reinstall.md#2-preparing-entware).

Use SSH key authentication for Entware administration and restrict SSH access to
your management network.

Keenetic Hue runs as isolated local processes inside Keenetic's Entware/Linux
environment under a single unified root:

```text
/opt/var/lib/keenetic-hue-system/
  releases/VERSION/  signed, version-specific application payloads
  manager/           stable CLI and recovery layer verifying the selected release
  config/            device, agent, management configuration and release trust public key
  secrets/           BLE bond and narrowly scoped token files
  state/             command journal, schema metadata and maintenance receipts
  run/               process ownership records, sockets and locks
  logs/              size-bounded operational and lifecycle logs
  backups/           private rollback copies and encrypted .huebak archives
  active.json        currently selected release and previous fallback release
```

`/opt/bin/huectl` is the operator entry point. `/opt/etc/init.d/S97keenetic-hue`
invokes `internal-service start` when Entware boots. The BLE agent and Tunnel are
supervised under separate lifecycle locks; restarting one component does not force
the other to restart. A PID file number is never trusted on its own—process
arguments, kernel `boot_id`, and process `startTicks` are verified together.

`config.example.json` is only a structural schema example showing field names and
paths. Its adapter and lamp addresses are intentional placeholders; do not copy it
as a working configuration. Supported reinstallation uses the signed release bundle
together with an encrypted `.huebak` backup from the same Hue installation—never
manually invent BLE bonds, MAC addresses, or tokens.

## Required Delivery Artifacts

- Signed `keenetic-hue-VERSION.huerel` software bundle.
- `keys/release-trust.pub` Ed25519 public verification key.
- `deploy/install_release.py` standalone trusted bootstrap/recovery installer.
- For migration to a new USB drive, your password-protected personal `.huebak` backup.

The software bundle carries no private configuration or credentials. On first
installation, verify the SHA-256 fingerprint of the public key through an
independent trusted channel; an existing trust key is never silently overwritten.
The public key fingerprint for this release line is:

```text
3eb6abbce938c6f75054da02ecca3cb291f576ef0633b5de54dd78fa2cd8a4be
```

The private signing key is never needed for router operation. Keep it in offline
secure storage—never on the router USB drive, inside a package, or in the repository.

## Preparing the Signed Software

After installing Entware and the pinned dependencies, copy the release artifacts via
SFTP into a `root`-only staging directory:

```sh
mkdir -p /opt/var/tmp/hue-install
chmod 700 /opt/var/tmp/hue-install
```

Place the `.huerel` archive and `release-trust.pub` in `/opt/var/tmp/hue-install`.
Copy `install_release.py` separately into `/opt/var/lib/keenetic-hue-trusted`,
created as `root` with `0700` permissions (verifying that parent directories are not
writable by unprivileged users).

On your trusted workstation, compute the SHA-256 digest of your reviewed
`deploy/install_release.py` copy (`Get-FileHash -Algorithm SHA256 .\deploy\install_release.py`
or `sha256sum deploy/install_release.py`). Replace `INSTALLER_SHA256`, `VERSION`,
and `PUBLIC_KEY_SHA256` below; the installer runs only if the hash check succeeds:

```sh
printf '%s  %s\n' 'INSTALLER_SHA256' /opt/var/lib/keenetic-hue-trusted/install_release.py | sha256sum -c - && \
python3 -I /opt/var/lib/keenetic-hue-trusted/install_release.py \
  /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel \
  --trust-key /opt/var/tmp/hue-install/release-trust.pub \
  --trust-key-sha256 PUBLIC_KEY_SHA256
```

This step verifies the Ed25519 signature, installs the release slot, and sets up the
manager entry points and service principals. On an empty installation it does not
generate lamp identities or tokens or auto-pair a bulb. If you are migrating an
existing installation, proceed to [reinstallation from an encrypted backup](reinstall.md).
If you have no backup, hardware-specific BLE pairing and private configuration must
be provisioned separately.

When migrating for the first time from the legacy three-directory layout, append
`--migrate-existing` to the command above. That option does not import arbitrary
paths: it backs up legacy entries, stops legacy owners, checks the adapter lock,
and copies credentials into the unified root while preserving permissions. Legacy
directories are moved under `backups/migration-*/legacy/` only after BLE and Tunnel
health pass on the new release; otherwise the migration rolls back. For normal
version upgrades on an existing unified root, use [`huectl update`](update-rollback.md).

## If the Recovery Layer Is Interrupted

After an interrupted maintenance operation, `huectl recover` or the next `S97` boot
processes pending receipts. If the CLI binary is temporarily absent during a manager
swap, re-run the same verified archive, public key, and standalone
`install_release.py` command above. Previous manager code is preserved under
`backups/bootstrap-*`. Keep copies of the signed archive and standalone installer
outside the router USB drive as well.

Never bypass signature or version checks to force recovery. Error outputs omit
sensitive values; first check that files match the expected version and trust key,
and verify private permissions, free disk space, and available RAM. Never factory-reset
the router or reset the Hue bulb during routine software recovery.
