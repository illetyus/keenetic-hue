# Rebuilding Cloudflare and Google Home Resources

This operator guide covers provisioning or recovering cloud resources after a full
cloud environment loss. Cloudflare Worker revision 31 passed live acceptance on the
documented personal installation. End-to-end setup on a brand-new Cloudflare and
Google Cloud account requires independent validation.

Do not delete working production resources merely to rehearse this guide. If only
the router's USB drive or Entware installation was replaced,
[reinstalling from your router `.huebak` backup](reinstall.md) is sufficient. A
router `.huebak` archive does not contain Cloudflare Durable Object storage, your
Google Cloud project, or write-only Worker secrets.

## 1. Identify the Recovery Scenario

| What Was Lost | Required Action |
| --- | --- |
| **Router USB / Entware only** | Install signed software + restore `.huebak` on the same adapter; verify local health first |
| **Tunnel only** | Create a new Cloudflare Tunnel & VPC Service, place the new `tunnel.token` on the router, update Worker binding |
| **Worker code only (Durable Objects intact)** | Re-deploy with the same Worker name, bindings, migrations, and secrets; do not delete the DO namespace |
| **Durable Object state or entire Cloudflare account** | Deploy fresh DOs and secrets; previous OAuth grants are gone, so re-link Google Home |
| **Google Cloud project / service account** | Create a new Smart Home project/service account, update Worker variables/secrets, and re-link Google Home |

## 2. Workstation Tooling and Configuration

From the repository root using a supported Node.js runtime:

```sh
cd cloud
npm ci
node node_modules/wrangler/bin/wrangler.js login
node node_modules/wrangler/bin/wrangler.js whoami
```

Select your target Cloudflare account. Copy `wrangler.tunnel.example.jsonc` to
`wrangler.personal.jsonc` (which is git-ignored) and fill in your VPC Service ID,
Google project ID, OAuth client ID, and full Worker HTTPS origin. Never write secret
values into JSONC files. Use the locked Wrangler version in `package-lock.json`.

## 3. Cloudflare Tunnel and Workers VPC Service

In the Cloudflare dashboard, navigate to **Workers VPC → Tunnels → Create** to
create a Tunnel. On the Keenetic router, always use the verified MIPS `cloudflared`
binary shipped inside the signed RC12 bundle—never run generic architecture
install commands from the dashboard on the router. Store the Tunnel token in your
password manager and copy it via SFTP as a `0600` root-owned file to
`/opt/var/lib/keenetic-hue-system/secrets/tunnel.token`, then run
`huectl restart tunnel`.

Under **VPC Services → Create**, select the Tunnel and configure the HTTP target as
`127.0.0.1` on port `8788`. Copy the resulting Service ID into the `HUE_TUNNEL`
binding in `wrangler.personal.jsonc`. This requires zero inbound port forwarding on
the router. Official documentation:
[Workers VPC Get Started](https://developers.cloudflare.com/workers-vpc/get-started/) ·
[Private HTTP API Example](https://developers.cloudflare.com/workers-vpc/examples/private-api/).

## 4. Provisioning Worker Secrets

Under **Settings → Variables and Secrets** for the Worker (or via `wrangler secret put`),
configure the following secrets (never log or commit their values):

| Secret Name | Source and Scope Boundary |
| --- | --- |
| `DEVICE_TOKEN` | Exact match with the router's `secrets/device.token`; authorizes commands over the Tunnel |
| `OPERATOR_TOKEN` | Independent random admin token (minimum 32 chars); never copied to the Keenetic router |
| `SETUP_ISSUER_TOKEN` | Exact match with the router's `secrets/setup-issuer.token`; authorizes `/admin/setup-code` only |
| `OAUTH_CLIENT_SECRET` | Matches the OAuth client secret configured in the Google Home Developer Console |
| `HOMEGRAPH_SERVICE_ACCOUNT` | Full JSON key for the Google HomeGraph service account; never stored on the router |

Note: the router's local `secrets/api.token` is for local loopback/Unix-socket calls
only and is never uploaded to the Cloudflare Worker.

To generate and provision `SETUP_ISSUER_TOKEN` interactively from the repository
root:

```sh
node tools/cloud-bootstrap.mjs generate-issuer
node tools/cloud-bootstrap.mjs provision-issuer
```

Then pair the router with `huectl configure-issuer --origin https://WORKER.ACCOUNT.workers.dev`
([details in the Google Home guide](google-home.md)).

## 5. Deploying the Worker and Durable Objects

Preserve the `HUE_RELAY` (`Relay`) and `HUE_AUTH` (`AuthStore`) Durable Object
bindings and migration tags in `wrangler.personal.jsonc`. Verify that
`PUBLIC_ORIGIN`, `GOOGLE_PROJECT_ID`, and `OAUTH_CLIENT_ID` match your environment,
then run:

```sh
node node_modules/wrangler/bin/wrangler.js deploy --dry-run --config wrangler.personal.jsonc
node node_modules/wrangler/bin/wrangler.js deploy --config wrangler.personal.jsonc
```

## 6. Google Home Developer Console Project

In the [Google Home Developer Console](https://console.home.google.com/), create or
select your personal project and configure a Cloud-to-cloud integration supporting
`LIGHT` (`OnOff` and `Brightness`). Set the OAuth 2.0 endpoints to your Worker
origin:

| Field | Value |
| --- | --- |
| OAuth Client ID | Same as Worker `OAUTH_CLIENT_ID` |
| OAuth Client Secret | Same as Worker `OAUTH_CLIENT_SECRET` |
| Authorization URL | `https://WORKER.ACCOUNT.workers.dev/oauth/authorize` |
| Token URL | `https://WORKER.ACCOUNT.workers.dev/oauth/token` |
| Fulfillment URL | `https://WORKER.ACCOUNT.workers.dev/fulfillment` |

Ensure the Google project ID matches `GOOGLE_PROJECT_ID` in the Worker so OAuth
redirect URI validation succeeds. Enable Test mode for your Google account.

## 7. HomeGraph State Reporting and Key Rotation

Enable the **HomeGraph API** in Google Cloud Console, create a service account with
the **Service Account OpenID Connect Identity Token Creator** role, generate a JSON
key, and upload it to the `HOMEGRAPH_SERVICE_ACCOUNT` Worker secret. Ensure
`REPORT_STATE_ENABLED` is enabled. Official guides:
[Report State](https://developers.home.google.com/cloud-to-cloud/integration/report-state#get_started) ·
[Request Sync](https://developers.home.google.com/cloud-to-cloud/integration/request-sync).

### Rotating the Service Account Key

Google recommends rotating user-managed service account keys at least every 90 days
([key rotation guide](https://docs.cloud.google.com/iam/docs/key-rotation)):

1. Create a new JSON key for the same service account and store it in your password
   manager.
2. Update `HOMEGRAPH_SERVICE_ACCOUNT` via
   `node node_modules/wrangler/bin/wrangler.js secret put HOMEGRAPH_SERVICE_ACCOUNT --config wrangler.personal.jsonc`.
3. Wait at least 6 minutes after deployment so active Durable Objects (`AuthStore`)
   exceed Cloudflare's 300-second deferred code-update window and reload the new
   secret.
4. Run the PowerShell verification snippet below from a trusted workstation to
   confirm `/admin/homegraph-check` (`authenticated: true`) and
   `/admin/report-check` (`reported: true`, `matched: true`) using `OPERATOR_TOKEN`:

```powershell
$origin = "https://WORKER.ACCOUNT.workers.dev"
$secureToken = Read-Host "OPERATOR_TOKEN" -AsSecureString
$tokenPointer = [IntPtr]::Zero
$operatorToken = $null
$headers = $null
$oldErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Stop"
try {
    $tokenPointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $operatorToken = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    $headers = @{ Authorization = "Bearer $operatorToken" }
    $auth = Invoke-RestMethod -Method Post -Uri "$origin/admin/homegraph-check" -Headers $headers -TimeoutSec 15
    if (-not $auth.authenticated) { throw "HOMEGRAPH_AUTH_NOT_CONFIRMED" }
    $report = Invoke-RestMethod -Method Post -Uri "$origin/admin/report-check" -Headers $headers -TimeoutSec 15
    if (-not $report.reported -or -not $report.matched) { throw "HOMEGRAPH_REPORT_NOT_CONFIRMED" }
    [pscustomobject]@{
        authenticated = $auth.authenticated
        reported = $report.reported
        matched = $report.matched
        lamp = $report.state
    } | ConvertTo-Json -Depth 4
}
finally {
    if ($tokenPointer -ne [IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    }
    if ($secureToken) { $secureToken.Dispose() }
    $operatorToken = $null
    $headers = $null
    $ErrorActionPreference = $oldErrorActionPreference
}
```

5. Disable the old key via `gcloud iam service-accounts keys disable OLD_KEY_ID ...`,
   wait up to 60 minutes for any cached short-lived token to expire, re-verify, and
   then delete the old key (`gcloud iam service-accounts keys delete OLD_KEY_ID ...`).

## 8. End-to-End Acceptance Sequence

1. `huectl health --live --json` on the Keenetic router (`healthy: true`).
2. Worker `/health` endpoint reachable over HTTPS.
3. `huectl setup-code` in an interactive SSH terminal.
4. Link `[test] Keenetic Hue` in the Google Home app.
5. Verify on/off and brightness commands over both LAN Wi-Fi and mobile data, and
   confirm HomeGraph state parity.
