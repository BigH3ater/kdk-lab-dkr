---
title: NUT — UPS monitoring & power-loss shutdown
host: kdk-dkr-01
stack: nut
tier: infra
status: live
storage: no
secrets: [kdk-ups-01-nut-upsd]
updated: 2026-09-15
tags: [homelab/stack, tier/infra, status/live]
---

# NUT — UPS monitoring & power-loss shutdown

Network UPS Tools monitors the **Eaton 5PX 1500** ("Computer Room") and shuts
down the two physical servers cleanly when the battery runs low during an
outage. The UPS is network-attached; NUT reads it over the LAN — nothing is
plugged into a host by USB.

**Related:** [[kdk-dkr-01]] · [[kdk-hyp-01]] · [[kdk-nas-01]] · [[home-assistant]]

## At a glance

| Field | Value |
|---|---|
| UPS | Eaton 5PX 1500, network management card (Mosaic firmware) at `10.1.3.63` |
| Driver | `netxml-ups` over `http://10.1.3.63` — the card serves its XML **anonymously**, so no device credentials are needed |
| Server | `upsd` in the `nut` stack on [[kdk-dkr-01]], listening on `:3493` (image `instantlinux/nut-upsd`, pinned by digest) |
| upsd user | `monuser` (password `op://kdk-ops/kdk-ups-01-nut-upsd`) |
| Clients | [[kdk-nas-01]] (TrueNAS native UPS service, SLAVE mode) and [[kdk-hyp-01]] (Proxmox `nut-client` upsmon) — both **secondary**, both shut down on low battery |
| Low-battery margin | `ignorelb` + `override.battery.charge.low=30` / `runtime.low=360` → LB fires with **~6 min** of runtime, not the card's ~3 min |
| Consumer | [[home-assistant]] NUT integration (battery, load, status, voltages) |

## Verify

```sh
# server: the UPS is being read
ssh kdkadmin@10.1.20.20 'sudo docker exec nut-upsd-1 upsc eaton-5px@localhost' | \
  grep -E 'ups.status|battery.charge:|ups.load:|battery.(charge|runtime).low:'
# expect: ups.status: OL CHRG (or OL), battery.charge.low: 30, battery.runtime.low: 360
```

```sh
# clients see the UPS
ssh kdkadmin@10.1.3.5   'upsc eaton-5px@10.1.20.20 | grep ups.status'   # TrueNAS
ssh kdkadmin@10.1.1.100 'systemctl is-active nut-monitor'               # Proxmox
```

`ups.status`: `OL` on line power, `OB` on battery, `OB LB` when low (shutdown
imminent), `CHRG` while charging.

## Troubleshooting

- **Driver won't connect** — confirm the host can reach the card:
  `curl -s http://10.1.3.63/product.xml` should return XML. The `netxml-ups`
  scheme is mandatory (`http://`), not just an IP.
- **A client isn't shutting down** — check its upsmon is connected
  (`upsc eaton-5px@10.1.20.20`) and that `monuser`'s password matches the
  server. On Proxmox: `journalctl -u nut-monitor`.
- **Shuts down too early / too late** — tune `stacks/nut/ups.conf`
  (`override.battery.runtime.low`), then redeploy the stack.
- Note the upsd runs in a container on [[kdk-dkr-01]] (a guest of
  [[kdk-hyp-01]]); when Proxmox powers off, upsd goes with it. That's fine —
  the clients are **secondary** and shut down on LB independently, so no
  "primary" host is required.

## Dependencies

Depends on the UPS NMC (`10.1.3.63`) and the `op-connect` secret injection at
deploy time. Consumed by [[home-assistant]] and, on power loss, by
[[kdk-hyp-01]] and [[kdk-nas-01]].

## Configuration

- Stack: `stacks/nut/` — `compose.yaml` (env: `DRIVER`, `PORT`, `NAME`,
  `API_USER`, `API_PASSWORD`), `.env.tpl` (op ref), `ups.conf` (driver +
  low-battery overrides, mounted to `/etc/nut/local/ups.conf`).
- TrueNAS: **Services → UPS**, mode *Remote Monitor (SLAVE)*, remote host
  `10.1.20.20:3493`, identifier `eaton-5px`, user `monuser`, shutdown
  *LOWBATT* (set via `midclt call ups.update`).
- Proxmox: `nut-client` package, `/etc/nut/nut.conf` `MODE=netclient`,
  `/etc/nut/upsmon.conf` `MONITOR eaton-5px@10.1.20.20:3493 1 monuser <pw>
  secondary` + `SHUTDOWNCMD "/sbin/shutdown -h +0"`, `nut-monitor` enabled.

## Secret Rotation

Rotate `op://kdk-ops/kdk-ups-01-nut-upsd` (the `monuser` password), then
redeploy the `nut` stack and update the two clients (TrueNAS `ups.update`
`monpwd`, Proxmox `upsmon.conf`). No device credentials to rotate — the card
is read anonymously.
