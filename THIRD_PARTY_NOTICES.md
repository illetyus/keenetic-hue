# Third-party components

The MIT license covers original Keenetic Hue code and artwork, not vendor
trademarks or third-party code.

| Component | Use | License |
| --- | --- | --- |
| [Bumble](https://github.com/google/bumble) | External pinned Python BLE dependency | Apache-2.0 |
| [cloudflared](https://github.com/cloudflare/cloudflared) | MIPS binary in RC12; source/patch/build inputs identified in the lock | Apache-2.0 and linked dependency notices |
| [Go](https://go.dev/LICENSE) | Runtime linked into cloudflared | Go BSD-style license |
| [e2fsprogs](https://git.kernel.org/pub/scm/fs/ext2/e2fsprogs.git/) | Optional maintenance tools obtained separately, not shipped in RC12/source | Respective upstream component licenses |
| Python, Entware, OpenSSL, libusb and Python packages | Installed separately through documented package sources | Respective upstream licenses |
| TypeScript/Cloudflare development packages | Downloaded separately using the npm lock | Respective package licenses |

[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt) preserves cloudflared/Go
license texts and notices for the 67 modules recorded in the actual binary,
including replacement modules. Keep it with the signed archive when redistributing
the binary. Recheck notices when dependencies change; this inventory is not a
legal compatibility opinion for arbitrary future builds.

Repository illustrations are original; no vendor logo or personal screenshot
is used. Private signing keys and personal configurations are not release assets.
