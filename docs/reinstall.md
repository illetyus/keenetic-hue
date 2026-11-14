# Reinstallation After USB or Entware Loss

This guide covers installing the signed RC15 release onto a new Entware USB drive
and restoring an encrypted `.huebak` backup from the same hardware setup. Always
verify the target physical disk before formatting a USB drive.

This workflow applies when keeping the **same USB Bluetooth adapter and the same
Hue bulb** with a valid personal `.huebak` backup. The bulb is not reset, and
existing Google Home and Cloudflare resources remain intact. The BLE agent checks
the physical adapter's public MAC address against the configured controller
identity and refuses to start if they do not match.

## 1. Prerequisites Kept Outside the Router USB Drive

- Your password-protected `.huebak` backup and its passphrase, stored in separate
  secure locations.
- The signed `.huerel` bundle, `keys/release-trust.pub`, the verified public key
  SHA-256 fingerprint, and the standalone `deploy/install_release.py` installer.
- This guide, [`requirements-router.lock.json`](../requirements-router.lock.json),
  and [`requirements-pure-router.txt`](../requirements-pure-router.txt).
- Access to your Cloudflare and Google Cloud accounts (a router `.huebak` archive
  does not back up write-only cloud Worker secrets).

A software bundle alone does not recreate a BLE bond, local tokens, or Google Home
links. If your personal `.huebak` archive is lost, BLE pairing and cloud
provisioning must be performed from scratch.

## 2. Preparing Entware

Format the USB drive as `ext4` and install Entware according to Keenetic's official
[OPKG support](https://help.keenetic.com/hc/en-us/articles/360000948719) and
Entware installation guides for your router model. Formatting erases existing data
on the target drive—verify the physical disk carefully.

Enable Keenetic's Open Package Support (OPKG) and EXT filesystem components, select
the USB drive on the OPKG page, and use the Entware installer matching your CPU
architecture. The verified RC15 package profile targets `mips-3.4` with CPython
3.13. Connect to Entware SSH on its configured port (commonly `222` or `22`
depending on your Keenetic SSH settings).

When a host key change is caused by a genuine USB/Entware reinstallation, verify
the new SSH host key fingerprint through a trusted local session before updating
your SSH client's `known_hosts`.

## 3. Installing Pinned Runtime Dependencies

In the new Entware SSH session:

```sh
opkg update
opkg print-architecture
opkg install python3 python3-pip python3-cryptography libusb-1.0 ca-bundle
python3 --version
python3 -c 'import cryptography; print(cryptography.__version__)'
```

`cryptography` is installed from Entware's prebuilt binary package (no Rust/C
compilation on the router). Copy `requirements-pure-router.txt` via SFTP into
`/opt/var/tmp/hue-install/` and install the pinned pure-Python dependencies and
Bumble:

```sh
python3 -m pip install --no-cache-dir --only-binary=:all: \
  -r /opt/var/tmp/hue-install/requirements-pure-router.txt
python3 -m pip install --no-cache-dir --no-deps bumble==0.0.236
```

Exact native and Python package versions are locked in
[`requirements-router.lock.json`](../requirements-router.lock.json). If upstream
Entware packages advance to a newer incompatible version, the signed installer
rejects the mismatch rather than running untested combinations; build and test a
new signed release instead of disabling dependency checks.

## 4. Installing the Signed Software

Run the [signed software bootstrap command](installation.md#preparing-the-signed-software).
Then copy your `.huebak` backup archive via SFTP into a `root`-only directory with
`0600` permissions:

```sh
mkdir -p /opt/var/lib/keenetic-hue-system/backups
chmod 700 /opt/var/lib/keenetic-hue-system/backups
chmod 600 /opt/var/lib/keenetic-hue-system/backups/hue-01.huebak
```

## 5. Dry-Run Verification and Initialization Restore

These commands accept the **10-file unified backup format** (`VERSION` is the
installed release ID, e.g. `0.2.0-rc.18`):

```sh
huectl restore /opt/var/lib/keenetic-hue-system/backups/hue-01.huebak \
  --initialize --release-id VERSION --dry-run
```

Enter your backup passphrase at the hidden terminal prompt. Once `verified: true`
is returned, run the initialization restore and live health check:

```sh
huectl restore /opt/var/lib/keenetic-hue-system/backups/hue-01.huebak \
  --initialize --release-id VERSION
huectl health --live --json
```

The dry-run writes no persistent files and starts no services. During application,
`--initialize` refuses to overwrite an existing configured installation. It
validates the Ed25519 signature, state schema, encrypted identity, exact file
manifest, token formats, and SQLite command journal schema inside an unprivileged
worker. The release trust public key inside the backup must match the installed
trust key.

Once the dedicated service principals start, `huectl` waits up to 90 seconds for
live BLE, Tunnel, and release-ownership health. This verifies physical USB adapter
identity and encrypted BLE readback without toggling the lamp on or off (the bulb
must have mains power during acceptance).

If an initialization restore is interrupted or fails health verification, `huectl`
stops the new processes, preserves partial state under `backups/initialize-*`, and
returns to the uninitialized state. After a power cut, `S97` startup or
`huectl recover` processes the receipt safely.

## 6. Google Home Acceptance

After `huectl health --live --json` reports `healthy: true` for the expected
`releaseId`, test a single command from Google Home. If your cloud Worker, Tunnel,
tokens, and OAuth grant were preserved, replacing the router USB drive does not
require re-linking Google Home. If the account was unlinked, issue a fresh code
with `huectl setup-code` and follow the [Google Home linking guide](google-home.md).

If cloud connectivity fails due to WAN, DNS, or revoked cloud tokens, local backup
files alone cannot fix the cloud side; see the [cloud rebuild guide](cloud-rebuild.md).
