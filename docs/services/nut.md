---
title: NUT — UPS monitoring & power-loss shutdown
host: kdk-dkr-03
stack: nut
tier: infra
status: live
storage: no
secrets: [kdk-ups-01-nut-upsd]
updated: 2026-10-17
tags: [homelab/stack, tier/infra, status/live]
---

# NUT — UPS monitoring & power-loss shutdown

Network UPS Tools monitors the **Eaton 5PX 1500** ("Computer Room") and shuts
down the two physical servers cleanly when the battery runs low during an
outage. The UPS is network-attached; NUT reads it over the LAN — nothing is
plugged into a host by USB.

**Related:** [[kdk-dkr-03]] · [[kdk-hyp-01]] · [[kdk-nas-01]] · [[home-assistant]]

## At a glance

| Field | Value |
|---|---|
| UPS | Eaton 5PX 1500, network management card (Mosaic firmware) at `10.1.3.63` |
| Driver | `netxml-ups` over `http://10.1.3.63` — the card serves its XML **anonymously**, so no device credentials are needed |
| Server | `upsd` in the `nut` stack on [[kdk-dkr-03]] (Pi 5), listening on `:3493` — `10.1.30.22` (VLAN 30) and `10.1.3.62` (VLAN 1, the card's L2). Image `instantlinux/nut-upsd`, pinned by digest (multi-arch) |
| UPS path | Host leg `eth1.1` = tagged VLAN 1 on kdk-csw-01 port 19, `10.1.3.62/24`, **no gateway** (`hosts/kdk-dkr-03/kdk-ups-l2.nmconnection`). NUT must reach the card on L2 — see Troubleshooting |
| upsd user | `monuser` (password `op://kdk-cluster/kdk-ups-01-nut-upsd`) |
| Clients | [[kdk-nas-01]] (TrueNAS native UPS service, SLAVE mode, → `10.1.3.62`) and [[kdk-hyp-01]] (Proxmox `nut-client` upsmon, → `10.1.30.22`) — both **secondary**, both shut down on low battery |
| Low-battery margin | `ignorelb` + `override.battery.charge.low=30` / `runtime.low=360` → LB fires with **~6 min** of runtime, not the card's ~3 min |
| Consumer | [[home-assistant]] NUT integration (battery, load, status, voltages) |

## Verify

```sh
# server: the UPS is being read
ssh kdkadmin@10.1.30.22 'sudo docker exec nut-upsd-1 upsc eaton-5px@localhost' | \
  grep -E 'ups.status|battery.charge:|ups.load:|battery.(charge|runtime).low:'
# expect: ups.status: OL CHRG (or OL), battery.charge.low: 30, battery.runtime.low: 360
```

```sh
# clients see the UPS
ssh kdkadmin@10.1.3.5   'upsc eaton-5px@10.1.3.62 | grep ups.status'    # TrueNAS
ssh kdkadmin@10.1.1.100 'systemctl is-active nut-monitor'               # Proxmox
```

`ups.status`: `OL` on line power, `OB` on battery, `OB LB` when low (shutdown
imminent), `CHRG` while charging.

## Troubleshooting

- **Driver won't connect** — confirm the host can reach the card:
  `curl -s http://10.1.3.63/product.xml` on kdk-dkr-03 should return XML, and
  `ip route get 10.1.3.63` must say `dev eth1.1` (L2). If it goes via the
  default gateway, `sudo nmcli connection up kdk-ups-l2`.
- **Why L2:** after its switch reboots (UniFi upgrades, power events) the card
  keeps answering ping and same-subnet TCP but **stops answering routed TCP**
  (2026-10-07, 2026-10-17: NUT on kdk-dkr-01 crash-looped for hours, no UPS
  protection). A card-only restart clears it; the L2 leg avoids needing one. The `netxml-ups`
  scheme is mandatory (`http://`), not just an IP.
- **Switch reboot → short blip, no action needed.** When kdk-asw-01 (the
  card's switch) or kdk-csw-01 (the Pi's uplink) reboots, the card is
  unreachable for ~2 min: upsd reports `Data stale`, upsmon on the clients
  logs "UPS unavailable", and everything reconnects by itself once the switch
  is back (validated live 2026-10-18 during the firmware downgrade: blind for
  1 min 58 s, self-recovered). The compose health check tolerates
  `STALE_GRACE` (180 s) of stale data before it restarts upsmon, and
  `ups.conf` lets the driver retry for 5 min at start-up, so the container no
  longer restart-loops through the outage. If it is still stale after the
  grace, the stock behaviour kicks in: upsmon is killed, the container
  restarts, and the driver reconnects when the card answers.
- **A client isn't shutting down** — check its upsmon is connected
  (`upsc eaton-5px@10.1.30.22`) and that `monuser`'s password matches the
  server. On Proxmox: `journalctl -u nut-monitor`.
- **Shuts down too early / too late** — tune `stacks/nut/ups.conf`
  (`override.battery.runtime.low`), then redeploy the stack.
- upsd runs on [[kdk-dkr-03]], a Pi that is **not** a guest of
  [[kdk-hyp-01]], so it keeps reporting while Proxmox shuts down. The clients
  are still **secondary** and shut down on LB independently.

## Incident 2026-10-26: upsd restart shut Proxmox down (UPS was fine)

**What happened.** 21:00 CDT gitops sync → deploy-changed redeployed every stack behind
HEAD, and the `nut` stack still carried `--force-recreate`, so upsd was recreated at
21:02:03 (the 6th time that day). The Eaton was in its monthly battery self-test
(`ups.test.interval` 2592000 s), so the last status the clients saw carried `CAL`.
`upsmon` 2.8.1 on [[kdk-hyp-01]] failed one poll at 21:02:18 and logged
`UPS [eaton-5px@10.1.30.22] was last known to be calibrating and currently is not
communicating, assuming dead` → `Executing automatic power-fail shutdown`. Proxmox shut
down cleanly with kdk-dkr-01 (Komodo, media, Tdarr, docs…) and kdk-dns-01 on it; iDRAC
draw 170 W → 23 W by 21:10. TrueNAS (upsmon 2.8.0, no CAL rule) only logged
"Communications lost" and reconnected 5 s later. UPS: `OL CHRG`, 100 %, 120 V throughout.

**Why DEADTIME does not help.** In `is_ups_critical()` the CAL (and OB) "assuming dead"
branch is gated only by `commstate == 0`, i.e. the first failed poll — in 2.8.1 and in
upstream master. `DEADTIME` only promotes a dead on-battery UPS to low-battery.

**Fix (landed).** (1) `--force-recreate` removed from the Komodo stack; (2) `ups.conf`
is a compose `config` (content-hashed) so real edits still recreate the container while
unrelated merges are a no-op; (3) `SERVER: primary` restored, hyp-01 `nut-monitor` and the
TrueNAS UPS service re-enabled. **Rule: never restart upsd without a reason** — the
clients cannot distinguish a restart from a dead UPS, and during a self-test or on
battery they will shut down on the spot.

**Residual exposure.** A dkr-03 reboot/crash or a network blip *during* the ~10-minute
monthly self-test (or a manual test) still triggers the rule on hyp-01. Options: disable
the card's automatic battery test (card UI at 10.1.3.63 → run tests manually when the
lab is quiet), or stop/`systemctl disable --now nut-monitor` on hyp-01 before planned
work on dkr-03 and re-enable after.

## Dependencies

Depends on the UPS NMC (`10.1.3.63`), the `eth1.1` VLAN 1 leg on
kdk-dkr-03, and the host-managed `/opt/kdk-lab/nut/.env` (the Pi has no `op`
CLI; Komodo pre_deploy copies it in). Consumed by [[home-assistant]] and, on power loss, by
[[kdk-hyp-01]] and [[kdk-nas-01]].

## Configuration

- Stack: `stacks/nut/` — `compose.yaml` (env: `DRIVER`, `PORT`, `NAME`,
  `API_USER`, `API_PASSWORD`), `.env.tpl` (op ref, documentation only), `ups.conf` (driver +
  low-battery overrides, mounted to `/etc/nut/local/ups.conf`).
- TrueNAS: **Services → UPS**, mode *Remote Monitor (SLAVE)*, remote host
  `10.1.3.62:3493` (same L2 — keeps the reply path symmetric), identifier `eaton-5px`, user `monuser`, shutdown
  *LOWBATT* (set via `midclt call ups.update`).
- Proxmox: `nut-client` package, `/etc/nut/nut.conf` `MODE=netclient`,
  `/etc/nut/upsmon.conf` `MONITOR eaton-5px@10.1.30.22:3493 1 monuser <pw>
  secondary` + `SHUTDOWNCMD "/sbin/shutdown -h +0"`, `nut-monitor` enabled.

## Secret Rotation

Rotate `op://kdk-cluster/kdk-ups-01-nut-upsd` (the `monuser` password), rewrite
kdk-dkr-03:`/opt/kdk-lab/nut/.env` (`NUT_CLIENT_PASSWORD=...`, 0600 root), then
redeploy the `nut` stack, update the Home Assistant NUT integration, and update the two clients (TrueNAS `ups.update`
`monpwd`, Proxmox `upsmon.conf`). No device credentials to rotate — the card
is read anonymously.
