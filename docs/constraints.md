# Resource and runtime acceptance

These are the unchanged engineering thresholds recorded in the existing 23 October project plan, not newly measured device capabilities:

- Idle CPU increase: at most 3 percentage points over the router baseline.
- Agent RSS: at most 48 MiB (49152 KiB).
- Remaining available RAM: at least max(16 MiB, 25% of total). With 250320 KiB RAM this is 62580 KiB.
- No observed increase in DNS failures or LAN connectivity failures. ICMP is unavailable in this workstation sandbox, so separately report TCP-connect results and their narrower scope.
- Warm command/state request p95 target: at most 3 seconds. Ordinary command expiry is 5 seconds; old queued writes are never applied after expiry.
- One BLE owner; no unencrypted control; only the known control characteristics; exact final combined-state readback; bounded transition convergence.
- Logs: rotation at 128 KiB, two backups. Journal: at most 256 commands, 30-minute retention.
- Private state directories 0700, API token/bond/config files 0600. No secret data in test reports or repository.
- No router restart, firmware/kernel changes, port forwarding, LAN/WAN API binding, DHCP, firewall or DNS changes.

Baseline uses three five-minute CPU/RAM windows. A foreground agent has a separate five-minute idle measurement plus bounded control/reconnect acceptance tests. This evidence does not establish 24-hour behavior or real boot/autostart.
