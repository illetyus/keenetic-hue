# Signed Updates, Repair, and Rollback

Use these procedures only with Ed25519-signed `.huerel` bundles whose public key
fingerprint and SHA-256 digest have been verified. Controlled rollback and normal
forward updates are verified on the Keenetic router.

Signed release manifests carry a monotonic `releaseSequence`. Normal updates reject
lower sequence numbers and reject different archive contents claiming the same
sequence. `huectl rollback` switches to the recorded previous verified slot without
lowering the highest accepted sequence floor.

Before updating, create a fresh password-protected [`.huebak` backup](backup-restore.md)
and store it off the router USB drive. Software bundles and personal `.huebak`
backups are separate: an update never overwrites private configuration or BLE bond
files from the bundle.

## Applying a Signed Update

Copy the signed `.huerel` archive via SFTP into a private `root`-only directory:

```sh
huectl health --live --json
huectl update /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel
huectl health --live --json
```

`huectl update` verifies the Ed25519 signature, complete file manifest and hashes,
target CPU architecture, Python ABI, pinned native/Python dependencies, and
persistent state schema. It stops both service principals, switches the selected
release via atomic file replacement (`active.json`), and starts the new `hue-agent`
and `hue-tunnel` processes. Live health checks require the responding process to be
owned by the newly selected release ID—an older HTTP listener cannot satisfy
readiness for the new slot.

`huectl` waits up to 60 seconds for live health acceptance. If health fails, the new
processes are stopped and the system automatically rolls back to the previous
verified release.

### Refreshing `root/manager` After `huectl update`

`/opt/bin/huectl update` intentionally preserves `root/manager` isolation: it
extracts `releases/VERSION/` and updates `active.json` while leaving
`/opt/var/lib/keenetic-hue-system/manager/`, `state/bootstrap.json`, and entry shims
(`/opt/bin/huectl`, `/opt/etc/init.d/S97keenetic-hue`, and
`/opt/etc/ndm/usb.d/50-keenetic-hue`) unchanged so the running control layer does
not self-modify during an update. Post-`0.2.0-rc.15` (`Unreleased`) builds surface
`bootstrapInSync` / `bootstrapSyncRequired` and `bootstrapReleaseId` in `huectl
update` output so operators can see when `root/manager` still belongs to an older
release.

To refresh `root/manager`, `state/bootstrap.json`, and the entry shims to match the
new release, re-run the SHA-256-verified standalone `deploy/install_release.py`
copy in `/opt/var/lib/keenetic-hue-trusted/install_release.py` (see
[installation](installation.md) for digest verification):

```sh
python3 -I /opt/var/lib/keenetic-hue-trusted/install_release.py \
  /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel \
  --trust-key /opt/var/tmp/hue-install/release-trust.pub \
  --trust-key-sha256 PUBLIC_KEY_SHA256
```

During bootstrap provisioning, existing entry shims are upgraded automatically when
their contents match the previously committed release's `service/` templates (or a
saved `entry-*.previous` backup from an interrupted bootstrap attempt); unrecognized
or externally modified entry files continue to fail closed with
`BOOTSTRAP_UNRELATED_ENTRY`.

## Rolling Back to the Previous Release

```sh
huectl rollback
huectl health --live --json
```

`huectl rollback` activates the previous verified release recorded in `active.json`.
Signed release slots and personal backups are not deleted automatically; keep at
least two verified release slots installed.

## Repairing a Damaged Release Slot

```sh
huectl repair /opt/var/tmp/hue-install/keenetic-hue-VERSION.huerel
huectl health --live --json
```

`huectl repair` requires the exact same signed archive for the target release ID. It
stops the running service principals, preserves mutable agent state (BLE bond and
command journal), moves damaged code/projection directories to a private quarantine,
reinstalls the verified files, and runs live health checks.

## Power Loss or Interrupted Maintenance

```sh
huectl recover
huectl health --live --json
```

Separate transactional receipts track bootstrap, clean-install initialization,
legacy migration, backup restore, package staging, and active slot selection. If an
operation is interrupted by power loss, `huectl recover` (or the next `S97` boot)
completes fully staged steps or safely rolls back incomplete transitions. If the
manager binary itself was interrupted mid-bootstrap, re-run the standalone
[installation command](installation.md).

`SERVICE_ALREADY_MANAGED` simply indicates that the expected release's supervisor
and child processes are already running. Use `huectl health --live --json` to verify
live BLE and Tunnel readiness.
