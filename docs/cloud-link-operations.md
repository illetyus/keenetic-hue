# Cloud Link — Daily Operations

This guide covers routine operation of the unified Keenetic Hue cloud bridge. Never
start a second standalone agent instance, manually edit token files under legacy
directories, or call deprecated internal HTTP endpoints on a running router.

## Safe First Check

```sh
huectl status
huectl health --live --json
```

`status` checks owned service processes; `health --live --json` probes the local
API, live BLE readback, and outbound Tunnel readiness. A healthy RC12 check returns
`healthy: true` and exit code `0`. Because local health queries do not call Google's
HomeGraph API, `reporting: unknown` (`NOT_CHECKED`) is normal and not an error.

## Which Component to Restart

- **Agent / BLE issue:** First check the USB port, Bluetooth adapter, and physical
  mains power to the Hue bulb; then if needed run `huectl restart agent` followed by
  `huectl health --live --json`.
- **Tunnel-only issue:** Check router WAN connectivity, DNS, and system clock; if
  needed run `huectl restart tunnel` followed by a live health check.
- **Local live health is healthy, but Google Home shows offline:** Do **not**
  immediately unlink and re-link your Google Home account. Diagnose the
  Worker/Tunnel/Google boundary separately using the [cloud rebuild guide](cloud-rebuild.md).

When linking a Google account, run `huectl setup-code` in an interactive SSH
terminal without disturbing existing configuration. The one-time code expires in
`600` seconds; never redirect it to a file or log. Details:
[Google Home account linking](google-home.md).

The Cloudflare Worker's public `/health` endpoint confirms only that the Worker
HTTPS origin is reachable; it does not prove router BLE, local API, or Google Home
command health.
