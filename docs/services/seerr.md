---
title: Seerr
host: kdk-dkr-dmz-01
stack: dmz-apps
tier: dmz
status: live
storage: yes
secrets: []
updated: 2026-11-04
tags: [homelab/stack, tier/dmz, status/live]
---

# Seerr

Media request UI, LAN/tailnet only (its router is on the DMZ Traefik's `websecure` entrypoint only, not the Cloudflare tunnel — see [[dmz-proxy]]), authenticating against Jellyfin accounts.

The same stack (`dmz-apps`) runs **Libreseerr**, the book request page: it hands requests to [[lazylibrarian]] (Chaptarr until its decommission on 2026-11-03) through internal Traefik (`extra_hosts` `lazylibrarian.kmkdp.com → 10.1.20.20`).

**Related:** [[kdk-dkr-dmz-01]] · [[jellyfin]] · [[media]] · [[lazylibrarian]] · [[dmz-proxy]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Users request TV/movies; Seerr forwards to Sonarr/Radarr |
| Status | 🟢 Live — Jellyfin link works; Sonarr/Radarr reachable from the container (DMZ → `10.1.20.20:443` open, `/ping` 200 on 2026-11-04) |
| Host | [[kdk-dkr-dmz-01]] |
| Endpoint | `https://seerr.kmkdp.com` |
| Image | `ghcr.io/seerr-team/seerr:v3.5.1` (3.5.0 moved Jellyfin auth to the `Authorization: MediaBrowser` header Jellyfin 12 requires) |
| Deploy | Komodo stack `dmz-apps` (git-linked) |
| Storage | `/mnt/appdata/jellyseerr` (NFS, same data as the k3s deploy) |
| Blast radius | No new requests; existing library unaffected |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://seerr.kmkdp.com/          # 200/307
ssh kdkadmin@192.168.191.20 'sudo docker exec seerr getent hosts sonarr.kmkdp.com'      # 10.1.20.20 (extra_hosts)
ssh kdkadmin@192.168.191.20 'sudo docker exec seerr wget -q -S -O /dev/null https://radarr.kmkdp.com/ping 2>&1 | grep -m1 HTTP'   # HTTP/1.1 200 OK
ssh kdkadmin@192.168.191.20 'sudo docker exec libreseerr getent hosts lazylibrarian.kmkdp.com'   # 10.1.20.20
```
In Settings, the Jellyfin, Sonarr, and Radarr integrations each test green.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Sonarr/Radarr test fails ("connection refused/timeout") | DMZ→internal path closed (UniFi rule or the DMZ host's DOCKER-USER exception for `10.1.20.20:443`, see [[host-firewall]]) | the `wget .../ping` line in Verify; the UniFi rule `192.168.191.20 → 10.1.20.20:443` must exist |
| arr hostnames don't resolve on the DMZ | public DNS points them at the DMZ, not internal Traefik | `extra_hosts` maps `sonarr/radarr.kmkdp.com → 10.1.20.20` |
| Jellyfin link broken | wrong `externalHostname` scheme | must be `https://jellyfin.kmkdp.com` (lowercase scheme) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[jellyfin]] | user auth + library status |
| Needs | [[media]] Sonarr/Radarr `/api` | request fulfilment (over DMZ → `10.1.20.20:443`) |
| Needs | [[dmz-proxy]] | ingress (LAN/tailnet) |

## Observability

No dashboard. `seerr.kmkdp.com` probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Listens on `5055`, joins `proxy-dmz`. It reaches Sonarr/Radarr over the internal Traefik `/api` bypass at `https://sonarr.kmkdp.com` and `radarr.kmkdp.com` (port 443), resolved via `extra_hosts` to `10.1.20.20`. Jellyfin is reached in-DMZ at `jellyfin:8096`.

## Configuration

| Path / setting | What it is |
|---|---|
| `/mnt/appdata/jellyseerr/settings.json` | Jellyfin + Sonarr/Radarr backends, API keys |
| `stacks/dmz-apps/compose.yaml` `extra_hosts` | pins arr hostnames to internal Traefik |

Edit `settings.json` only with Seerr stopped (it rewrites the file on shutdown).

## Secret Rotation

N/A — no credential of its own; holds arr API keys inside `settings.json` (rotate those in the arr apps, then update here).

### Libreseerr admin reset

Libreseerr (`stacks/dmz-apps`, `/mnt/appdata/libreseerr:/app/data`) keeps users in
`users.json` with werkzeug scrypt hashes and reloads the file on every request.
Local admin credential: `op://kdk-ops/libreseerr-admin`. To reset it: new value in
1Password first, then

```bash
op read op://kdk-ops/libreseerr-admin/password | tr -d '\n' | ssh kdkadmin@192.168.191.20 '
  H=$(sudo docker exec -i libreseerr python3 -c "import sys; from werkzeug.security import generate_password_hash; print(generate_password_hash(sys.stdin.read()))")
  sudo docker stop libreseerr
  sudo env H="$H" python3 -c "
import json,os; h=os.environ[\"H\"]; p=\"/mnt/appdata/libreseerr/users.json\"; us=json.load(open(p))
[u.update(password_hash=h) for u in us if u[\"username\"]==\"admin\"]
json.dump(us,open(p+\".tmp\",\"w\"),indent=2); os.replace(p+\".tmp\",p)"
  sudo docker start libreseerr'
```

Check: `POST /api/auth/login` (not `/api/login`) inside the container returns
`{"role":"admin","success":true}`. Pass the hash through the environment or a
file, never as stdin to a heredoc script (that produced an empty hash once).

OIDC users: Libreseerr reads the ID Token claims and only asks UserInfo when they
are empty, so Authelia carries a `claims_policies.libreseerr` entry that copies
`preferred_username` into the ID Token for this client ([identity](identity.md#claims-policies)).
Without it every SSO login created a user named by its `sub` UUID. OIDC settings
(issuer, client id/secret, `auto_create_users`) live in `config.json` → `oidc`; the
client secret plaintext is `op://kdk-cluster/libreseerr-oidc/password`, its digest
`authelia-app-secrets/libreseerr_client_secret` (rotation: [rotate-a-secret](../runbooks/rotate-a-secret.md)).

## User Guide

Users log in at `seerr.kmkdp.com` with their Jellyfin account and request titles; approved requests flow to Sonarr/Radarr for download.
