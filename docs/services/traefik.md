---
title: Traefik (internal)
host: kdk-dkr-01
stack: proxy
tier: infra
status: live
storage: yes
secrets: [cloudflare-dns-acme-hosts]
updated: 2026-09-12
tags: [homelab/stack, tier/infra, status/live]
---

# Traefik — internal ingress

Terminates TLS and host-routes every internal `*.kmkdp.com` service on kdk-dkr-01, gating most behind Authelia ForwardAuth.

**Related:** [[kdk-dkr-01]] · [[identity]] · [[dmz-proxy]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Reverse proxy + ACME wildcard certs for internal services |
| Status | 🟢 Live — serves 80/443, wildcard cert valid |
| Host | [[kdk-dkr-01]], ports `80`/`443` |
| Image | `traefik:v3.7` |
| Deploy | Komodo stack `proxy` (git-linked) |
| Cert | `kmkdp.com` + `*.kmkdp.com`, ACME DNS-01 via Cloudflare, stored `/opt/kdk-lab/proxy/letsencrypt/acme.json` |
| Blast radius | Every internal `*.kmkdp.com` service is unreachable |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://komodo.kmkdp.com/    # 200/302, valid TLS
docker logs traefik 2>&1 | grep -i acme | tail -3                     # certificate obtained/renewed
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| A service 404s at Traefik | Container not on the `proxy` network or missing labels | `docker inspect <c> --format '{{json .NetworkSettings.Networks}}'` shows `proxy` |
| Cert not issued | Cloudflare DNS token missing/invalid | `docker logs traefik 2>&1 | grep -i "acme\|cloudflare"` |
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

Docker provider (label discovery on the `proxy` network) + file provider (`./dynamic`). `web:80` redirects to `websecure:443`; `websecure` carries the `cf` resolver wildcard. `forwardauth@file` (in `dynamic/middlewares.yml`) forwards auth to `https://sso.kmkdp.com`.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/proxy/compose.yaml` | Entrypoints, ACME resolver, provider config |
| `stacks/proxy/dynamic/middlewares.yml` | `forwardauth@file` → Authelia |
| Per-service labels | `traefik.http.routers.<name>.rule` + `.middlewares` |

Add a service: join the external `proxy` network and add Traefik labels. No DNS change — the wildcard rewrite already points here.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `cloudflare-dns-acme-hosts` (kdk-ops) | ACME DNS-01 token | 🟡 external party | yes — rotate in Cloudflare + 1P, redeploy; existing cert stays valid until renewal |

## User Guide

N/A — headless. The Traefik API dashboard is enabled but not published.
