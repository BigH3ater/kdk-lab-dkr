---
title: Apps stack
host: kdk-dkr-01
stack: apps
tier: internal
status: live
storage: yes
secrets: [kdk-dns-adguard-home-admin]
updated: 2026-10-30
tags: [homelab/stack, tier/internal, status/live]
---

# Apps stack

Small internal utilities on kdk-dkr-01: time, AdGuard replication, a Proton SMTP bridge, and the Roborock local server (plus a `certs-dumper` sidecar that writes Roborock's TLS cert) — six services in all.

**Related:** [[kdk-dkr-01]] · [[traefik]]

## At a glance

| Field | Value |
|---|---|
| Purpose | NTP, AdGuard sync, proton-bridge, roborock (rmfakecloud retired 2026-10-31) |
| Status | 🟢 Live |
| Host | [[kdk-dkr-01]] |
| Endpoints | host-port services only (no Traefik routes since rmfakecloud was retired) |
| Deploy | Komodo stack `apps` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/apps/<svc>` |
| Blast radius | Per-service; no shared failure domain |

## Verify

```bash
docker ps --filter name=ntp --filter name=adguardhome-sync --format '{{.Names}} {{.Status}}'
docker logs adguardhome-sync 2>&1 | tail -3          # last sync ok to dns-02/03
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| AdGuard replicas drift | sync creds wrong or origin down | `docker logs adguardhome-sync` shows auth/connection error |
| SMTP on `10.1.20.20:25` closes with no banner; `socat ... connect 127.0.0.1:1025: Connection refused` in `docker logs proton-bridge` | Bridge stores its own ports in its vault and moved off 1025/1143 (seen 2026-10-31 after a re-login: 1026/1144); the image's socat is hard-wired to 1025/1143 | `nsenter -t $(docker inspect proton-bridge --format {{.State.Pid}}) -n ss -lntp`. Fix: stop the container, run the CLI once under `script` with the same `/root` volume and send `change smtp-port` 1025 and `change imap-port` 1143, then start it (the in-container `faketty` FIFO does not work, as the CLI needs a tty) |
| Roborock devices offline | TLS/config not migrated | check `/opt/kdk-lab/apps/roborock/{config.toml,tls}` present |
| Roborock cert stale / `TLSCertExpiringSoon` for `api-slave` | lego DNS-01 failing, hook not copying, or roborock not restarted since renewal | `docker logs roborock-cert-lego` (ACME order or "no renewal needed"); `echo \| openssl s_client -connect 10.1.20.20:555 -servername api-slave.kmkdp.com 2>/dev/null \| openssl x509 -noout -subject -dates`; run procedure `roborock-cert-refresh` |

## Roborock TLS certificate

The vacuum (VLAN 30, internet blocked) opens TLS to `api-slave.kmkdp.com:555` on the roborock container and validates the cert, so an expired cert = vacuum offline. It must stay a **dedicated `CN=api-slave.kmkdp.com`** cert (the vacuum is not trusted to accept a `*.kmkdp.com` wildcard SAN). A **standalone lego ACME client** renews it:

**Why not the internal Traefik.** Traefik's ACME provider skips any domain already covered by a certificate in its single, global TLS store — the `*.kmkdp.com` wildcard included — no matter which resolver or storage file holds it. The wildcard covers `api-slave.kmkdp.com`, so a dedicated resolver never issued a cert (verified live: its `acme.json` stayed 0 bytes and `:443` served `CN=kmkdp.com`). A self-contained ACME client has no such store, so it issues the dedicated cert the vacuum needs.

1. A separate one-shot stack **`roborock-cert`** (`stacks/roborock-cert`, `goacme/lego`) runs `lego run --dns cloudflare --domains api-slave.kmkdp.com --renew-days 30 --deploy-hook "sh /hook.sh"` against its own state dir `/opt/kdk-lab/apps/roborock/lego`. v5's `run` is idempotent: it obtains on the first run and renews only when within 30 days of expiry (else exits 0, no ACME call). Cloudflare **DNS-01** (same `op://kdk-cluster/cloudflare-dns-acme-hosts/credential` token Traefik uses, as `CLOUDFLARE_DNS_API_TOKEN`) means nothing needs to be reachable on the name.
2. lego's **deploy hook** (`stacks/roborock-cert/hook.sh`) fires only when a cert is written and copies `certificates/api-slave.kmkdp.com.{crt,key}` → `/opt/kdk-lab/apps/roborock/tls/{tls.crt,tls.key}` (the names `config.toml` `[tls] mode="provided"` points at), root-owned `0644`/`0600`. On a no-op run nothing is touched, so the valid cert is never clobbered.
3. The roborock server reads the cert only at start, so the **`roborock-cert-refresh`** Komodo procedure (`komodo/resources.toml`, cron `0 40 4 1,15`) runs the `roborock-cert` one-shot (stage 1) then restarts the `roborock` service (stage 2) on the 1st + 15th. Renewal happens 30 days before expiry, so the served cert is never more than ~15 days behind.

Verify (read-only, from `kdk-dkr-01`):

```bash
docker logs roborock-cert-lego 2>&1 | tail      # last run: obtained/renewed api-slave.kmkdp.com, or "no renewal needed"
echo | openssl s_client -connect 10.1.20.20:555 -servername api-slave.kmkdp.com 2>/dev/null \
  | openssl x509 -noout -subject -dates          # CN=api-slave.kmkdp.com, notAfter advances after renewal+restart
```

Alerting: `api-slave.kmkdp.com:555/healthz` is probed by `job="blackbox-http-app"`; `TLSCertExpiringSoon` now matches `job=~"blackbox-.*"` (was `blackbox-service` only, which never covered it) and warns at 14 days. **Rollback:** `git revert`; the hand-copied cert is untouched until the first successful hook run (and valid until 2026-11-28).

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | AdGuard trio (dns-01/02/03) | sync has no origin/replicas |
| Needed by | AdGuard replicas | rules/records stop propagating |

## Observability

No dashboard. `ntp` (123/udp) and `proton-bridge` (25) are host-port; alerting → Pushover.

## Architecture

| Service | Image | Port |
|---|---|---|
| ntp | `cturra/ntp` | `123/udp` |
| adguardhome-sync | `bakito/adguardhome-sync:v0.9.5` | none (cron `*/15`) |
| proton-bridge | `shenxn/protonmail-bridge:3.19.0-1` | `10.1.20.20:25` |
| roborock | `python-roborock/local_roborock_server:1.2.1` | `555`, `8881` |

`proton-bridge` is an in-lab Proton SMTP relay on `10.1.20.20:25`. **Authelia's notifier sends through it** (`smtp://10.1.20.20:25`, STARTTLS with `skip_verify`, sender `no-reply@kmkdp.com`; `stacks/identity/authelia/configuration.yml`, 2026-10-29) — this replaced the old direct `submission://smtp.protonmail.ch:587`, and Authelia's `disable_startup_check` is now `false`, so it checks the relay at boot and fails loudly if it is down. Alertmanager still notifies through Pushover, not mail. The relay needs a Proton account logged in; if its session log shows `Loading users count=0`, log one in with `sudo docker exec -it proton-bridge protonmail-bridge --cli` → `login`. Its self-updates land on a `tmpfs` (PR #143): the image's own bridge binary runs at every start, and newer bridge builds come only through Renovate.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/apps/compose.yaml` | All six services (`certs-dumper` is a TLS sidecar for roborock) |
| AdGuard origin/replicas | `10.1.1.120` → `.121`/`.122` |
| `/opt/kdk-lab/apps/roborock/` | `config.toml` + `tls/` (migrated from k8s Secrets) |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-dns-adguard-home-admin` | AdGuard API auth (all three) | 🟢 self-service | yes — rotate in AdGuard + 1P, redeploy |

## User Guide

AdGuard sync is unattended; the others are headless integrations.

**rmfakecloud retired 2026-10-31.** Tablet content now moves over SSH (`obsidian-to-koreader`,
`readwise-koreader`, `koreader-highlights`, `remarkable-ocr`), so the self-hosted reMarkable cloud and
the `readwise-to-remarkable` native-reader highlights job were removed. Its data is archived on
kdk-dkr-01 at `/opt/kdk-lab/apps/rmfakecloud.retired-20261031`, and the reMarkable cloud domains are
blackholed to `0.0.0.0` by `dns-reconcile`, so the tablet stays cloud-free.
