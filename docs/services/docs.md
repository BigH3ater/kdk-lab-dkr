---
title: Docs site
host: kdk-dkr-01
stack: docs
tier: internal
status: live
storage: yes
secrets: []
updated: 2026-09-12
tags: [homelab/stack, tier/internal, status/live]
---

# Docs site

This site: `docs/` in the repo, built with Quartz and served by nginx; the Komodo webhook rebuilds it on every push to main.

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Published one-page-per-host/service reference |
| Status | 🟢 Live — `curl https://docs.kmkdp.com/healthz` returns `ok` |
| Host | [[kdk-dkr-01]] |
| Endpoint | `https://docs.kmkdp.com` (ForwardAuth-gated) |
| Images | builder `node:24-bookworm-slim` + Quartz `v4.5.2`, server `nginx:1.29-alpine` |
| Deploy | Komodo stack `docs` (git-linked); builder runs once per deploy |
| Storage | shared `site` volume (built output); source is the repo clone |
| Blast radius | Docs offline; no operational impact |

## Verify

```bash
curl -s https://docs.kmkdp.com/healthz            # ok
docker logs docs-builder-1 2>&1 | grep BUILD-DONE # present after a successful build
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Site shows stale content | builder didn't rerun | `docker logs docs-builder-1 | tail` — expect a recent `BUILD-DONE` |
| Build fails on push | Quartz/markdown error | `docker logs docs-builder-1` shows the failing file |
| 404 after a rename | link points at a deleted basename | fix the `[[wikilink]]` or relative link |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[traefik]] | ingress + TLS + ForwardAuth |
| Needs | [[komodo]] webhook | no rebuild on push |

## Observability

No dashboard; `/healthz` is the liveness check.

## Architecture

The builder clones Quartz, copies `../../docs` from the repo into `content`, builds into the shared `site` volume, then idles. `nginx` serves that volume on `80` behind Traefik.

## Configuration

| Path / setting | What it is |
|---|---|
| `docs/` | Site content (this vault) |
| `stacks/docs/compose.yaml` | Builder command + Quartz version pin |
| `stacks/docs/nginx.conf` | Server config incl. `/healthz` |

## User Guide

Browse `docs.kmkdp.com` (behind SSO). Edit a `.md` under `docs/`, push to main, and the webhook redeploys the stack, which rebuilds the site.
