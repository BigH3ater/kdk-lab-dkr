---
title: Traefik (DMZ) + cloudflared
host: kdk-dkr-dmz-01
stack: dmz-proxy
tier: dmz
status: live
storage: yes
secrets: [cloudflare-dns-acme-hosts, cloudflare-tunnel-credentials]
updated: 2026-10-27
tags: [homelab/stack, tier/dmz, status/live]
---

# Traefik (DMZ) + cloudflared

DMZ ingress: Traefik terminates TLS and host-routes the public services, and cloudflared connects the Cloudflare tunnel to it.

**Related:** [[kdk-dkr-dmz-01]] · [[identity]] · [[jellyfin]] · [[immich]] · [[seerr]] · [[wizarr]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Reverse proxy + tunnel for DMZ/public services |
| Status | 🟢 Live — 443 serves; tunnel connected |
| Host | [[kdk-dkr-dmz-01]] (192.168.191.20), ports `80`/`443` |
| Images | `traefik:v3.7`, `cloudflare/cloudflared:2026.10.3` |
| Deploy | Komodo stack `dmz-proxy` (git-linked, `op inject` pre_deploy) |
| Cert | `kmkdp.com` + `*.kmkdp.com`, ACME DNS-01 via Cloudflare |
| Blast radius | All DMZ + public services (sso, jellyfin, immich, seerr, wizarr, users) unreachable |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://sso.kmkdp.com/     # 200, valid TLS
docker logs cloudflared 2>&1 | grep -i "registered tunnel" | tail -2
```
Off-LAN, `sso`/`seerr`/`jellyfin`/`immich`/`wizarr` answer with a `server: cloudflare` header.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Public hostname 502 | tunnel down or token invalid | `docker logs cloudflared` — connection/registration errors |
| Wrong client IP in logs | trustedIPs missing the source | `forwardedHeaders.trustedIPs` includes `10.1.20.20/32,192.168.191.0/24,10.211.2.0/24` (the last = cloudflared on proxy-dmz, so Cloudflare's visitor IP survives into Jellyfin's known-proxies logic) |
| Cert not issued | Cloudflare DNS token invalid | `docker logs traefik 2>&1 | grep -i acme` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | Cloudflare (tunnel + DNS API) | no public access, no cert renewal |
| Needed by | [[identity]], [[jellyfin]], [[immich]], [[seerr]], [[wizarr]] | ingress + TLS on the DMZ |

## Observability

No dashboard. Public hostnames probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Docker provider on the `proxy-dmz` network; `web:80` redirects to `websecure:443`. cloudflared runs as uid `65532` and dials out to Cloudflare, forwarding to this Traefik. The tunnel's public hostnames are dashboard-managed (operator-owned); Traefik host-routes what arrives. Most public services carry their own auth; the one exception is [[wizarr]], whose **admin** router declares a label-defined `wizarr-auth` ForwardAuth middleware (→ `http://authelia:9091/api/authz/forward-auth`) while its invitee paths stay open on a higher-priority public router.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/dmz-proxy/compose.yaml` | Traefik entrypoints/resolver + cloudflared |
| `forwardedHeaders.trustedIPs` | `10.1.20.20/32,192.168.191.0/24` |
| Cloudflare tunnel | dashboard-managed; forwards to `https://<this-traefik>` |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `cloudflare-dns-acme-hosts` (kdk-ops) | ACME DNS-01 token | 🟡 external party | rotate in Cloudflare + 1P, redeploy |
| `cloudflare-tunnel-credentials` (kdk-cluster) | tunnel token | 🟡 external party | rotate the tunnel token in Cloudflare + 1P, redeploy |

## User Guide

N/A — headless ingress.
