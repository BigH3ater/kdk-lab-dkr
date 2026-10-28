---
title: Traefik (DMZ) + cloudflared
host: kdk-dkr-dmz-01
stack: dmz-proxy
tier: dmz
status: live
storage: yes
secrets: [cloudflare-dns-acme-hosts, cloudflare-tunnel-credentials]
updated: 2026-10-28
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
| Host | [[kdk-dkr-dmz-01]] (192.168.191.20), published ports `80`/`443`; tunnel entrypoint `8443` on `proxy-dmz` only |
| Images | `traefik:v3.7.16`, `cloudflare/cloudflared:2026.10.5` (pinned; Renovate proposes updates) |
| Fixed IPs (`proxy-dmz` 10.211.2.0/24) | cloudflared `10.211.2.2`, traefik `10.211.2.3`; dynamic range `10.211.2.128/25` |
| Deploy | Komodo stack `dmz-proxy` (git-linked, `op inject` pre_deploy) |
| Cert | `kmkdp.com` + `*.kmkdp.com`, ACME DNS-01 via Cloudflare |
| Blast radius | All DMZ + public services (sso, jellyfin, immich, seerr, wizarr, users) unreachable |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://sso.kmkdp.com/     # 200, valid TLS
docker logs cloudflared 2>&1 | grep -i "registered tunnel" | tail -2
```
Off-LAN, only the tunnel hostnames (`sso`, `jellyfin`; `wizarr` invitee paths once published) answer, with a `server: cloudflare` header.

```bash
docker inspect cloudflared traefik -f '{{.Name}} {{(index .NetworkSettings.Networks "proxy-dmz").IPAddress}}'   # .2 / .3
docker inspect traefik -f '{{.State.Health.Status}}'                                                          # healthy
# tunnel entrypoint serves only tunnel routers (throwaway client on proxy-dmz; 8443 is not published):
docker run --rm --network proxy-dmz curlimages/curl:8.17.0 -sk -o /dev/null -w '%{http_code}\n' \
  --resolve immich.kmkdp.com:8443:10.211.2.3 https://immich.kmkdp.com:8443/        # 404
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Public hostname 502 | tunnel down or token invalid | `docker logs cloudflared` — connection/registration errors |
| Tunnel visitors logged as `10.211.2.2`, no `Cf-*` in the access log | Cloudflare public hostname still points at `https://traefik` (:443, websecure: cloudflared untrusted + strip-cf) | access log `entryPointName` must be `tunnel` for tunnel traffic; fix the origin to `https://traefik:8443` |
| Tunnel hostname returns 404 | router lacks `entrypoints=websecure,tunnel` (websecure is the only default entrypoint) | `docker inspect <app> -f '{{json .Config.Labels}}' \| grep entrypoints` |
| cloudflared cannot reach Traefik after a recreate | `.2`/`.3` taken, or `proxy-dmz` recreated without the ipam block | `docker network inspect proxy-dmz -f '{{json .IPAM.Config}}'` |
| Every DMZ route 404/500 after a Traefik restart | `strip-cf@file` missing (dynamic dir not mounted / YAML error): websecure references it on every router | `docker logs traefik 2>&1 \| grep -i 'strip-cf\|file provider'` |
| Cert not issued | Cloudflare DNS token invalid | `docker logs traefik 2>&1 | grep -i acme` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | Cloudflare (tunnel + DNS API) | no public access, no cert renewal |
| Needs | `identity_default` network (created by [[identity]]); Traefik joins it for the lldap UI router | stack fails to start; `users.kmkdp.com` 502 |
| Needed by | [[identity]], [[jellyfin]], [[immich]], [[seerr]], [[wizarr]] | ingress + TLS on the DMZ |

## Observability

No dashboard. Public hostnames probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Docker provider on the `proxy-dmz` network; `web:80` redirects to `websecure:443`. cloudflared runs as uid `65532` (read-only rootfs, no caps, metrics on `127.0.0.1:20241`) and dials out to Cloudflare, forwarding to this Traefik's `tunnel` entrypoint.

Two HTTPS entrypoints, split by who may set client-IP headers (2026-10-28, edge audit H-1 / Jellyfin M4 / Authelia M7):

| Entrypoint | Listens | Trusts `X-Forwarded-*` from | `Cf-*` headers | Routers |
|---|---|---|---|---|
| `websecure` | `:443`, published | `10.1.20.20/32` (internal Traefik), `10.1.30.21/32` (Pi Traefik, ForwardAuth) | **stripped** (`strip-cf@file`) | every DMZ router (the only default entrypoint) |
| `tunnel` | `:8443`, `proxy-dmz` only | `10.211.2.2/32` (cloudflared); `notAppendXForwardedFor` so backends see the visitor IP one hop behind Traefik | kept (real visitor IP/geo) | only routers labelled `entrypoints=websecure,tunnel`: `authelia` (sso), `jellyfin`, `wizarr-public` |

Both entrypoints delete alias-named request headers (`aliasHeadersStrategy=delete`, e.g. `Cf_Connecting_Ip`), so WSGI-style backends cannot be fed a header that strip-cf did not see. Every DMZ router carries an explicit `entrypoints` label; a Cloudflare hostname accidentally pointed at an internal-only host (immich, recipes, users …) gets Traefik's 404 on the tunnel entrypoint, not the app. Tunnel traffic in Loki: `entryPointName="tunnel"` or `ClientAddr=~"10\\.211\\.2\\.2:.*"`. The tunnel's public hostnames are dashboard-managed (operator-owned); Traefik host-routes what arrives. Most public services carry their own auth; the one exception is [[wizarr]], whose **admin** router declares a label-defined `wizarr-auth` ForwardAuth middleware (→ `http://authelia:9091/api/authz/forward-auth`) while its invitee paths stay open on a higher-priority public router.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/dmz-proxy/compose.yaml` | Traefik entrypoints/resolver + cloudflared; `proxy-dmz` ipam (`10.211.2.0/24`, pinned `.2`/`.3`) |
| `stacks/dmz-proxy/dynamic/strip-cf.yml` | `strip-cf` headers middleware (file provider, watched directory) |
| `websecure` `forwardedHeaders.trustedIPs` | `10.1.20.20/32,10.1.30.21/32` |
| `tunnel` `forwardedHeaders.trustedIPs` | `10.211.2.2/32` |
| Cloudflare tunnel | dashboard-managed; public hostnames → `https://traefik:8443`, *Origin Server Name* = the hostname, No TLS Verify off |
| `/ping` | `127.0.0.1:8080` inside the container (healthcheck only) |

### Adding a public hostname

1. Cloudflare Zero Trust → Networks → Tunnels → kdk tunnel → Public Hostname → Add: `<host>.kmkdp.com`, service `https://traefik:8443`, TLS → *Origin Server Name* `<host>.kmkdp.com`, *No TLS Verify* off. Check the DNS record is orange-cloud.
2. Router label in the app's compose: `traefik.http.routers.<r>.entrypoints: "websecure,tunnel"` (without it the tunnel returns 404).
3. Authelia: access-control rule / OIDC client for the host if it uses SSO (`stacks/identity/authelia/configuration.yml`).
4. Loki edge rules / Grafana: add the host to the tunnel alerts (keyed on `ClientAddr=~"10\\.211\\.2\\.2:.*"`).
5. Run the `homelab-security-review` skill for the service before flipping the hostname on.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `cloudflare-dns-acme-hosts` (kdk-ops) | ACME DNS-01 token | 🟡 external party | rotate in Cloudflare + 1P, redeploy |
| `cloudflare-tunnel-credentials` (kdk-cluster) | tunnel token | 🟡 external party | rotate the tunnel token in Cloudflare + 1P, redeploy |

## User Guide

N/A — headless ingress.
