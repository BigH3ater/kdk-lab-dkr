---
title: Media stack
host: kdk-dkr-01
stack: media
tier: internal
status: live
storage: yes
secrets: [kdk-dee-01-api-token]
updated: 2026-11-04
tags: [homelab/stack, tier/internal, status/live]
---

# Media stack

Acquisition, subtitles, request-library management and GPU transcode on kdk-dkr-01; every UI is behind Authelia ForwardAuth.

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[jellyfin]] · [[seerr]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Sonarr/Radarr/Prowlarr/Bazarr/SABnzbd + Tdarr NVENC transcode |
| Status | 🟢 Live — each UI 302s to SSO; arr health clean |
| Host | [[kdk-dkr-01]] |
| Endpoints | `prowlarr / sabnzbd / sonarr / radarr / bazarr / lazylibrarian / tdarr .kmkdp.com` |
| Deploy | Komodo stack `media` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/media/<app>` (config); bulk on `/mnt/media/{tv,movies,staging,quarantine}` + `/mnt/dee-exchange` |
| Blast radius | No new downloads/imports/transcodes; Jellyfin playback unaffected |

## Verify

**Laptop** (`op` signed in; the last line runs on kdk-dkr-01 over ssh)

```bash
KEY=$(ssh kdkadmin@10.1.20.20 "sudo sed -n 's:.*<ApiKey>\(.*\)</ApiKey>.*:\1:p' /opt/kdk-lab/media/sonarr/config.xml")   # Sonarr API key (host config, no 1Password item yet)
curl -s -o /dev/null -w '%{http_code}\n' https://sonarr.kmkdp.com/          # 302 (SSO gate)
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Api-Key: $KEY" https://sonarr.kmkdp.com/api/v3/health  # 200 (bypass)
ssh kdkadmin@10.1.20.20 'sudo docker exec media-tdarr-node-1 nvidia-smi -L'      # GTX 1050 Ti listed
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| arr indexer/download-client points at old cluster | stale `*.media.svc.cluster.local` host | in the app, host should be `sabnzbd` / `prowlarr` (compose service name) |
| SABnzbd returns 403 to arr | Host header not whitelisted | `grep host_whitelist /opt/kdk-lab/media/sabnzbd/sabnzbd.ini` includes `sabnzbd` |
| SABnzbd page refused from a phone on Tailscale; log `Refused connection from: ::ffff:10.208.15.8 (X-Forwarded-For: 100.64.x.x)` | `local_ranges` lacks the tailnet `100.64.0.0/10` (SABnzbd checks the Traefik-set X-Forwarded-For) | `sudo docker logs --since 1h media-sabnzbd-1 2>&1 \| grep -c 'Refused connection'` is 0; `grep -E '^(inet_exposure\|local_ranges)' /opt/kdk-lab/media/sabnzbd/sabnzbd.ini` matches the Configuration row |
| Seerr can't reach Sonarr/Radarr API | DMZ→internal firewall closed | operator must open `192.168.191.20 → 10.1.20.20:443` |
| Tdarr idle with jobs queued | staged jobs pinned to dead nodes / 0 workers | clear StagedJSONDB; set transcode workers to 1 |
| Transcode drops Atmos to native E-AC3 | both DEE slots busy or down (503 from `dee-lb`) | check both wrappers' /healthz + HAProxy stats on `127.0.0.1:8405` (kdk-dkr-01); the flow waits up to 1 h for a slot before the native E-AC3 fallback |
| Transcode error "Subtitle codec 94213 is not supported" | MP4 source with tx3g subs, container step not conforming | both MKV steps in the flow have `forceConform=true`; re-queue |
| AV1 file stays AV1 / 4K remux keeps foreign audio | flow bypass (bitrate gate / old DV hold) | `scripts/tdarr-flow-sync.sh diff` shows repo flow == live (see tdarr runbook) |
| Hindi/Tamil/Telugu movie appears in Radarr (or gets downloaded) | IMDb Popular / Top 250 import lists carry Indian titles; Radarr cannot filter IMDb lists by language | `arr-list-guard` procedure (every 6 h) removes untagged, file-less ones with an exclusion; already-downloaded ones are reported, delete by hand. Want one? request it in Seerr (tagged) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[traefik]] + [[identity]] | ingress and ForwardAuth |
| Needs | `/mnt/media` (NFS, kdk-nas-01) | no library or staging access |
| Needs | [[kdk-dee-01]] + kdk-dee-02 wrappers, fronted by `dee-lb` (HAProxy, `10.1.20.20:8484`) | Dolby Atmos audio conversion in Tdarr — two single-slot encode VMs, `leastconn`, `maxconn 1` each |
| Needed by | [[jellyfin]], [[seerr]] | library content and request fulfilment |

## Observability

No dashboard. Host probes via [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Each app joins `default` + `proxy`; ports are `prowlarr 9696`, `sabnzbd 8080`, `sonarr 8989`, `radarr 7878`, `bazarr 6767`, `lazylibrarian 5299`, `tdarr 8265`. [LazyLibrarian](lazylibrarian.md) is the only book / audiobook manager (it replaced Chaptarr, decommissioned 2026-11-03), behind the DMZ [Libreseerr](seerr.md) and adding one requested title at a time; it is the first service here on the compose-baseline hardening anchor. Tdarr also publishes node RPC `8266` (on `10.1.20.20`) for the external RGB-Nebula 4090 Windows node, which is no longer registered (see the [Tdarr runbook](../runbooks/tdarr-troubleshooting.md)); transcoding runs on the local node. Sonarr/Radarr have a higher-priority `/api` router without ForwardAuth so the DMZ Seerr can use the API with a key. The local node `tdarr-node` (`kdk-tdarr-nvenc-01`) uses the NVIDIA runtime and the GTX 1050 Ti; flow encodes to HEVC with NVENC and re-encodes any AV1 source (software decode). Tdarr 3.x (Rust engine).

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/media/compose.yaml` | Images, volumes, Traefik + `/api`-bypass labels |
| `*__AUTH__METHOD=External` | arr UIs trust Authelia; API keys still gate `/api` |
| `<app>/config.xml` `<ApiKey>` | per-app API key (not in 1P) |
| `sabnzbd/sabnzbd.ini` `host_whitelist` | accepted Host headers |
| `sabnzbd/sabnzbd.ini` `inet_exposure` / `local_ranges` (host-managed, set in Config → Special) | `inet_exposure = 0` (no external access) and `local_ranges = 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10`. A non-empty `local_ranges` **replaces** SABnzbd's built-in private ranges, so the three RFC 1918 blocks must stay listed or LAN clients, Traefik and the arrs are refused. `100.64.0.0/10` is the tailnet: the subnet router does not masquerade, so tailnet clients reach Traefik with their 100.64 address, Traefik writes it into X-Forwarded-For (no `forwardedHeaders.trustedIPs`, so client-sent XFF is dropped), and SABnzbd (`verify_xff_header = 1`) requires every XFF hop to be local. Tailnet clients are trusted admin sources here, and Authelia (`two_factor`, `group:internal-users`) plus SABnzbd's own login still gate the UI, so the narrow range is preferred over raising `inet_exposure`. Set 2026-11-04 after iPhone-on-Tailscale refusals |
| Radarr → Settings → Import Lists | 15 lists (13 TMDb directors, IMDb Popular, IMDb Top 250); `listSyncLevel=disabled`. Language block = `stacks/arr-list-guard` (`LANGUAGES` env) |


### Wizarr (invites) — MOVED

Wizarr left the media stack on 2026-10-27 for its own internet-facing DMZ stack
(`stacks/wizarr` on kdk-dkr-dmz-01). Its LDAP settings table, the public-path allowlist,
and the `wizarr-sync` invite-reconcile automation now live in
[Wizarr](wizarr.md).

### Trailarr

`stacks/trailarr` on kdk-dkr-01 downloads trailers for Radarr/Sonarr titles and
files them as Jellyfin local trailers. It now has its own page — see
[Trailarr](trailarr.md) for the new-titles-only policy, the web-login reset
procedure, and the connection-refresh steps after an arr API-key rotation.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-dee-01-api-token` | Tdarr DEE wrapper auth | 🟢 self-service | yes — rotate + redeploy |
| arr API keys | app-to-app auth | 🟢 self-service | regenerate in each app UI; update consumers (Prowlarr, Seerr, Bazarr, Trailarr, Libreseerr for LazyLibrarian); arr-upgrade-search / arr-list-guard / tdarr-remediate read `config.xml` and need nothing |
| `kdk-ops/trailarr-admin` | Trailarr web login | 🟢 self-service | one-off `WEBUI_PASSWORD` start (see [Trailarr](trailarr.md)) |

## User Guide

Prowlarr feeds indexers to Sonarr/Radarr; downloads land via SABnzbd into `/mnt/media/staging`, are imported to `tv`/`movies`, then Tdarr transcodes. Jellyfin invites are issued by [Wizarr](wizarr.md) (now on the DMZ).
