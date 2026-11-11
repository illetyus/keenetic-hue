# Encrypted Backups, Restore, and Migrating to a New USB Drive

When working with a signed release bundle, always use the release's documented
installer command, public key SHA-256 fingerprint, and release ID. Before applying
a personal `.huebak` backup, run `--dry-run` to decrypt and validate the archive
without modifying running services.

## Creating a Personal Encrypted Backup

In an interactive Entware SSH terminal on the Keenetic router:

```sh
mkdir -p /opt/var/lib/keenetic-hue-system/backups
chmod 700 /opt/var/lib/keenetic-hue-system/backups
huectl backup /opt/var/lib/keenetic-hue-system/backups/my-backup.huebak
```

Enter a backup passphrase of at least 16 characters when prompted. Store the
passphrase and the `.huebak` archive separately in secure offline storage; never
commit either to GitHub. `huectl backup` refuses to overwrite an existing file.
Copy the `.huebak` file off the router USB drive via SFTP before performing any USB
maintenance.

The backup archive contains canonical configurations, adapter/lamp identities, the
BLE bond, local API/Tunnel/setup-issuer tokens, the release trust public key, and
the command journal snapshot taken from the active dedicated-UID agent. Logs,
caches, and release binaries are excluded. SSH private/host keys, Keenetic OS
settings, Cloudflare Durable Object state, and Google Cloud project credentials are
outside the scope of `.huebak`.

## Restoring on the Same Running Installation

First run a non-disruptive verification dry-run (services remain running):

```sh
huectl restore /secure/path/my-backup.huebak --dry-run
```

Enter the backup passphrase at the hidden terminal prompt. A valid archive returns
`verified: true` and `installationChanged: false`.

Once verified, apply the restore and check live health:

```sh
huectl restore /secure/path/my-backup.huebak
huectl health --live --json
```

`huectl` stops the services, preserves current state for rollback, applies the
backup, and verifies live health of the restarted release. If startup fails after a
normal restore, `huectl` automatically rolls back to the pre-restore state. If a
restore is interrupted by power loss, do not move files manually—run:

```sh
huectl restore --recover
huectl health --live --json
```

A missing or corrupted active runtime journal is never silently regenerated from an
older master file; startup halts safely. For unrecoverable active storage loss, use
a verified `.huebak` backup with the clean-install workflow below.

## Migrating to a New USB Drive / Clean Entware Root

1. Keep your encrypted `.huebak` archive, its passphrase, the signed `.huerel`
   release bundle, and the public key SHA-256 fingerprint stored outside the USB
   drive. Keep the same USB Bluetooth adapter and Hue bulb.
2. Set up Entware on the new ext4 USB drive matching the Python and native package
   versions in [`requirements-router.lock.json`](../requirements-router.lock.json).
   The signed installer does not upgrade OS packages automatically. Verify the new
   SSH host key fingerprint through a trusted local interface.
3. Reboot the Keenetic router normally after setting up the new USB drive so any
   transient native user/group entries from a previous session are cleared.
4. Run the standalone `install_release.py` bootstrap installer with the public key
   fingerprint ([installation guide](installation.md)). It verifies the signature
   and prepares the release slot, `huectl` entry point, and dedicated service
   principals. Do not reset or re-pair the Hue bulb.
5. Copy your `.huebak` archive into a private `0700` directory and run the
   initialization restore using the installed `RELEASE_ID`:

```sh
huectl restore /secure/path/my-backup.huebak --initialize --release-id RELEASE_ID --dry-run
huectl restore /secure/path/my-backup.huebak --initialize --release-id RELEASE_ID
huectl health --live --json
```

Replace `RELEASE_ID` with the installed release version (for example `0.2.0-rc.16`).
Clean initialization does not copy numeric Linux UIDs from the backup; it checks
service accounts for conflicts and assigns ownership safely without running
recursive `chown` across the unified root or USB mount.

`ACCOUNT_UNPROFILED_NAME` indicates that service account names exist in `/opt/etc`
without a matching trusted identity profile. The installer refuses to adopt or
delete unprofiled accounts. On a fresh USB/Entware setup, reboot the router and
re-run the signed installer. Using a different USB Bluetooth controller requires a
separate deliberate pairing and migration; a BLE bond from one controller is not
assumed to work on another.

## Code Rollback and Slot Repair

If a previous verified release slot is recorded:

```sh
huectl rollback
huectl health --live --json
```

If a signed code slot or reproducible runtime projection has damaged permissions,
repair it using the verified `.huerel` bundle for the **same** release version:

```sh
huectl repair /secure/path/same-version.huerel
```

`huectl repair` stops the services, preserves active agent state (BLE bond and
command journal), moves reproducible code/config/credential projection and
run/log/cache directories into a root-private quarantine, and rebuilds them from
the signed archive and root master configuration. If interrupted, run:

```sh
huectl recover
huectl health --live --json
```

Never share or commit quarantine directories, as they may contain private token
projections. Code repair, backup restore, and clean USB migration are distinct
operations—do not unlink your Google Home account unless OAuth credentials were
actually lost.
