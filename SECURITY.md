# Security policy

The maintained router candidate is **0.2.0-rc.17** on the profile in
[README](README.md). The Worker is versioned separately. Retaining older slots
for rollback does not promise ongoing vulnerability fixes for them.

## Report privately

Use **Security → Report a vulnerability** when private reporting is available.
If that entry is unavailable, open only a generic issue requesting a private
contact channel. Do not include exploit details or credentials in that request.
No response deadline or bounty is promised.

Include affected versions, minimal dummy-input reproduction, expected/observed
behavior and the trust boundary. Never send live tokens, private keys, BLE bonds,
setup codes, personal backups or unredacted configuration. Release public keys
and artifact hashes are public information.

Do not bypass signatures, SSH host-key verification, ownership or dedicated
service identities to resolve an error. Native tests require disposable,
authorized environments. Reviews, tests and live acceptance establish specific
properties; they do not certify every deployment as vulnerability-free.
