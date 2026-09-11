---
title: Traefik (internal)
---
# Traefik — internal ingress

Terminates TLS for internal `*.kmkdp.com` services on kdk-dkr-01.

| | |
|---|---|
| Host | kdk-dkr-01 :80/:443 |
| Stack | `stacks/proxy` — traefik v3.7, docker labels + file provider |
| Cert | wildcard kmkdp.com + *.kmkdp.com, ACME DNS-01 via Cloudflare |
| Secrets | cloudflare-dns-acme-hosts (kdk-ops) |
| Middleware | forwardauth@file → https://sso.kmkdp.com |

Add a service: join the external `proxy` docker network + traefik labels.
No DNS change once the wildcard rewrite points here.
