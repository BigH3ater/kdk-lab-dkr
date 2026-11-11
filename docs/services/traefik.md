---
title: Traefik (internal)
host: kdk-dkr-01
stack: proxy
tier: infra
status: live
storage: yes
secrets: [cloudflare-dns-acme-hosts]
updated: 2026-11-11
tags: [homelab/stack, tier/infra, status/live]
---

# Traefik — internal ingress

Terminates TLS and host-routes every internal `*.kmkdp.com` service on kdk-dkr-01, gating most behind Authelia ForwardAuth.

This is one of **three** Traefiks: this one (internal apps, `traefik:v3.7.17`), the DMZ one (`traefik:v3.7.18` since 2026-11-11) with the Cloudflare tunnel entrypoint and `strip-cf` ([[dmz-proxy]]), and the file-provider-only one on the Home Assistant Pi ([[kdk-dkr-02]], `stacks/home-assistant`, `traefik:v3.7.17`).

**Related:** [[kdk-dkr-01]] · [[identity]] · [[dmz-proxy]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Reverse proxy + ACME wildcard certs for internal services |
| Status | 🟢 Live — serves 80/443, wildcard cert valid |
| Host | [[kdk-dkr-01]], ports `80`/`443` bound to `10.1.20.20` only (VLAN 20; not the storage NIC, not docker0). LAN, tailnet (routed to 10.1.20.20) and DMZ→443 all arrive there |
| Image | `traefik:v3.7.17` + `ghcr.io/tecnativa/docker-socket-proxy:v0.5.0` (both digest-pinned; Renovate proposes updates) |
| Deploy | Komodo stack `proxy` (git-linked) |
| Cert | `kmkdp.com` + `*.kmkdp.com`, ACME DNS-01 via Cloudflare, stored `/opt/kdk-lab/proxy/letsencrypt/acme.json` |
| Blast radius | Every internal `*.kmkdp.com` service is unreachable |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code} ssl=%{ssl_verify_result}\n' https://komodo.kmkdp.com/   # 302 ssl=0 (valid TLS)
echo | openssl s_client -connect 10.1.20.20:443 -servername komodo.kmkdp.com 2>/dev/null | openssl x509 -noout -subject -enddate
#   subject=CN = kmkdp.com, notAfter more than 30 days away (renewal is automatic at 30 days)
ssh kdkadmin@10.1.20.20 'sudo docker ps --filter name=traefik --format "{{.Names}} {{.Status}}"'   # traefik Up, traefik-socket-proxy Up (healthy)
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| A service 404s at Traefik | Container not on the `proxy` network or missing labels | `sudo docker inspect <c> --format '{{json .NetworkSettings.Networks}}'` shows `proxy` |
| Cert not issued | Cloudflare DNS token missing/invalid | `sudo docker logs traefik 2>&1 \| grep -i "acme\|cloudflare"` (quiet when the stored cert is valid: ACME only logs at issue/renewal) |
| Every Docker-label route 404s, file routes (grafana, alertmanager) still work | socket proxy down, so the Docker provider sees no containers | `sudo docker ps --filter name=traefik-socket-proxy` healthy; `sudo docker logs traefik 2>&1 \| grep -i 'provider.*docker'` |
| UI reachable without login | Router missing `forwardauth@file` middleware | check the service's `traefik.http.routers.<r>.middlewares` label |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | Cloudflare API | ACME DNS-01 renewal fails (cert expires) |
| Needs | [[identity]] (Authelia) | ForwardAuth can't validate; gated UIs 500 |
| Needed by | all internal services | no ingress or TLS |

## Observability

No dashboard. `blackbox-http-app` on [[kdk-mon-01]] probes routed hosts; alerts → Pushover.

## Architecture

Docker provider (label discovery on the `proxy` network, `exposedbydefault=false`) through a **read-only socket proxy**: Traefik reads the Docker API at `tcp://socket-proxy:2375` on the internal `sockproxy` network (no egress); the socket proxy (`traefik-socket-proxy`) is the stack's only `docker.sock` mount and allows GET/HEAD on containers, events, version and ping only (`POST=0`). Plus the file provider (`./dynamic`, watched). `web:80` redirects to `websecure:443`; `websecure` carries the `cf` resolver wildcard. `forwardauth@file` (in `dynamic/middlewares.yml`) forwards auth to `https://sso.kmkdp.com/api/authz/forward-auth` with `maxResponseBodySize` 1 MiB. Access log is JSON to stdout (headers dropped except `User-Agent`), shipped by Alloy to Loki (`{container="traefik"} | json`). **Access log policy:** query strings are dropped (`--accesslog.fields.queryparameters.defaultmode=drop`), so `RequestPath` is the path only; callers such as Seerr and Libreseerr pass API keys as `?apikey=`, and Traefik v3.7 cannot drop single parameters. Do not re-enable query parameters; a rule that needs a query value must read the app's own log instead.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/proxy/compose.yaml` | Entrypoints, ACME resolver, provider config |
| `stacks/proxy/dynamic/middlewares.yml` | `forwardauth@file` → Authelia |
| `stacks/proxy/dynamic/grafana.yml` | `grafana.kmkdp.com` → `http://10.1.20.30:3000` (no ForwardAuth here: Grafana does its own Authelia OIDC login) |
| `stacks/proxy/dynamic/alertmanager.yml` | `alertmanager.kmkdp.com` → `http://10.1.20.30:9093` behind `forwardauth` (2026-10-28, PR #85; DNS from `EXTRA` in dns-reconcile) |
| Per-service labels | `traefik.http.routers.<name>.rule` + `.middlewares` |

Add a service: join the external `proxy` network and add Traefik labels. No DNS change — the wildcard rewrite already points here.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `cloudflare-dns-acme-hosts` (kdk-ops) | ACME DNS-01 token | 🟡 external party | yes — rotate in Cloudflare + 1P, redeploy; existing cert stays valid until renewal |

## User Guide

N/A — headless. The Traefik API dashboard is enabled but not published.
