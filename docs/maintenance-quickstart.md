# Daily Maintenance — Quick Reference

This quick reference covers routine operation of the unified Keenetic Hue
installation and RC12 `huectl` management commands. On an empty Entware root,
follow the [installation guide](installation.md) first.

Run these commands over SSH in an interactive Entware terminal. Never pass
passwords, setup codes, or tokens on the command line; `huectl` prompts privately
when secret input is required.

## Health and Status

```sh
huectl status
huectl health --live --json
```

In the `health` output, `healthy: true` and exit code `0` confirm healthy local
state. Neither `status` nor `health` sends a write command to the bulb, and they
do not by themselves prove cloud HomeGraph report acceptance.

## Issuing a New One-Time Google Home Setup Code

Required only when linking or re-linking your Google Home account:

```sh
huectl setup-code
```

The command displays a one-time code valid for `600` seconds. Enter the code in
the Google Home app under the `[test] Keenetic Hue` provider. The command refuses
to run outside a TTY—never redirect the code to a file or log. No AI or MCP tool
is required for routine code issuance. Details: [Google Home account linking](google-home.md).

## Creating an Encrypted Backup

```sh
mkdir -p /opt/var/lib/keenetic-hue-system/backups
chmod 700 /opt/var/lib/keenetic-hue-system/backups
huectl backup /opt/var/lib/keenetic-hue-system/backups/hue-20261108.huebak
```

Replace the date in the filename with the current date. `huectl` prompts twice for
a backup passphrase (minimum 16 characters). Store the passphrase in a password
manager and copy the `.huebak` archive off the router USB drive to secure offline
storage. Never share backup archives, passphrases, or tokens in support messages.
Details: [Encrypted backup and restore](backup-restore.md).

## Restoring a Backup to an Existing Installation

Always run a verification dry-run first, followed by the actual restore and live
health check:

```sh
huectl restore /path/to/backup.huebak --dry-run
huectl restore /path/to/backup.huebak
huectl health --live --json
```

The passphrase is requested via a hidden terminal prompt. This path applies to an
existing installation with the same hardware profile; for a clean Entware root or
after USB drive loss, follow the [reinstallation guide](reinstall.md).

## Signed Updates and Rollback

Create a fresh backup before updating. Copy the signed `.huerel` package via SFTP
into `/opt/var/tmp/hue-install/` and replace `VERSION` with the actual release
filename:

```sh
huectl health --live --json
huectl update /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel
huectl health --live --json
```

If the new release does not pass live health verification within the acceptance
window, `huectl` automatically stops the new processes and rolls back to the
previous verified release. To manually switch back to the recorded previous
release:

```sh
huectl rollback
huectl health --live --json
```

To inspect and recover an interrupted local maintenance operation:

```sh
huectl recover
huectl health --live --json
```

Detailed release sequence rules, compatibility paths, and failure semantics:
[Signed updates, repair, and rollback](update-rollback.md).

## Reinstallation

Use the [reinstallation guide](reinstall.md) only after Entware USB storage loss.
Prepare a replacement USB drive separately from the working drive. Resetting or
re-pairing the Hue bulb is not part of normal reinstallation when using the same
hardware and a valid encrypted `.huebak` backup.
