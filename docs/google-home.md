# Google Home Account Linking and Setup Code Management

Live account re-linking, device synchronization (`SYNC`), and subsequent lamp
command execution are verified against Cloudflare Worker revision 31. The Worker
serves a 10-minute signed authorization form bound to a strict same-origin cookie.
Each setup code is single-use, and a successful re-link atomically supersedes any
previous Google Home OAuth grant.

This guide covers operating an existing Cloudflare and Google Home setup. On a
first-time deployment, the Worker, Durable Objects, Cloudflare Tunnel/VPC Service,
and Google Home Cloud-to-cloud project must be provisioned first; see the
[cloud rebuild guide](cloud-rebuild.md).

## Issuing a New One-Time Setup Code

In an interactive Keenetic Entware SSH terminal:

```sh
huectl setup-code
```

The command outputs a 26-character one-time code valid for `600` seconds. Issuing a
new code invalidates any previous unused code without revoking an already linked
Google Home account. If stdout is redirected to a file or pipe, `huectl` refuses to
issue a code and exits with `SETUP_CODE_REQUIRES_TERMINAL` (exit code `2`).

On your phone, signed into the same test Google account, open the Google Home app
→ **Add → Works with Google Home**, select **`[test] Keenetic Hue`**, enter the
one-time code on the authorization page, and approve linking. If the code expires,
run `huectl setup-code` again. You do not need to generate setup codes during normal
daily operation once linked.

Never paste setup codes, issuer tokens, or API tokens into support messages.

## Provisioning or Rotating the Setup-Code Issuer Token

The setup-issuer credential (`SETUP_ISSUER_TOKEN`) authorizes only `/admin/setup-code`
requests to mint short-lived setup codes. It cannot control the lamp, revoke OAuth
grants, or manage HomeGraph. General Cloudflare API tokens and `OPERATOR_TOKEN` are
never stored on the Keenetic router.

On your workstation from the repository root, install the locked Wrangler version
and authenticate with Cloudflare:

```sh
cd cloud
npm ci
node node_modules/wrangler/bin/wrangler.js login
cd ..
node tools/cloud-bootstrap.mjs generate-issuer
```

`generate-issuer` prints a 64-character hex token (32 random bytes) only in an
interactive terminal. Save it in your password manager, then provision it to the
Worker:

```sh
node tools/cloud-bootstrap.mjs provision-issuer
```

Paste the token at the hidden prompt. The helper passes the secret to Wrangler via
`stdin` (never via command-line arguments or repository files) and updates
`SETUP_ISSUER_TOKEN` on the `keenetic-hue-relay` Worker.

Next, in your Keenetic Entware SSH terminal, configure the router with your Worker's
HTTPS origin:

```sh
huectl configure-issuer --origin https://WORKER.ACCOUNT.workers.dev
```

Paste the same token at the hidden prompt. Do not include paths, query strings, or
credentials in the `--origin` URL. `huectl` verifies remote setup-code issuance
before replacing the local token file; if verification fails, the existing local
credential is preserved. A successful check returns `configured: true` and
`authority: setup-code-only` without displaying the test code. Afterward, run
`huectl setup-code` whenever you need a user-facing code.

While rotating the issuer token, setup-code generation is briefly unavailable
between the Cloudflare update and the router update, but existing Google Home OAuth
tokens and lamp commands continue to work uninterrupted. To revoke the router's
ability to mint setup codes at any time, rotate or delete `SETUP_ISSUER_TOKEN` in
Cloudflare.

## Reinstallation Boundaries

- **Router USB/Entware replaced only:** Restore from your `.huebak` backup and keep
  using the existing cloud resources ([reinstallation guide](reinstall.md)).
- **Worker or Tunnel lost:** Re-create the Tunnel/VPC Service and Worker bindings
  and provision a fresh Tunnel token ([cloud rebuild guide](cloud-rebuild.md)).
- **Google Cloud project or service account key lost:** Re-create the HomeGraph
  service account key and update the `HOMEGRAPH_SERVICE_ACCOUNT` Worker secret.
