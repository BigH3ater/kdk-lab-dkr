---
title: Calibre
host: kdk-dkr-01
stack: calibre
tier: internal
status: live
storage: yes
secrets: []
updated: 2026-10-30
tags: [homelab/service, tier/internal, status/live, books]
---

# Calibre (calibre.kmkdp.com)

Internal Calibre Content Server on **kdk-dkr-01**: serves an ebook library over
**OPDS** so the reMarkable's KOReader pulls books when it comes online (pull
model, replacing the old push-only `book-sync` path). Deliberately **no
Authelia** — OPDS clients can't do SSO, so the LAN/Tailscale is the gate (same
exposure as the legacy raw `:8081`).

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[media]]

## At a glance

| Field | Value |
|---|---|
| Purpose | OPDS ebook library for KOReader / reMarkable (pull model) |
| URL | https://calibre.kmkdp.com (Traefik, **no** Authelia); OPDS catalog `https://calibre.kmkdp.com/opds`; legacy tailnet `http://10.1.20.20:8081/opds` |
| Host | [[kdk-dkr-01]] |
| Stack | `stacks/calibre` (`calibre-server` + `calibre-import`) |
| Image | `kdk/calibre:latest` (built locally — `auto_pull = false`, `run_build = true`) |
| Data | `/opt/kdk-lab/calibre/library` (Calibre library, swept by the `/opt/kdk-lab` rsync) |
| Deploy | Komodo stack `calibre` (`deploy = true`), webhook + 15-min poll |
| Blast radius | reMarkable can't pull new books; existing downloads unaffected |

## Verify

```bash
ssh kdkadmin@10.1.20.20 'docker ps --filter name=calibre --format "{{.Names}} {{.Status}}"'   # calibre-server + calibre-import Up
curl -s -o /dev/null -w '%{http_code}\n' https://calibre.kmkdp.com/opds                        # 200 (no SSO redirect)
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| `calibre-import exited 137` on every deploy | two services building the same tag, or missing `init` on the loop | handled in compose (`provenance: false`, `init: true`); `docker logs calibre-import` |
| OPDS empty | no books imported yet / wrong source | `docker logs calibre-import` — it scans `/mnt/media/books/ebook` every 900 s |
| 502 at the URL | `calibre-server` not up | `docker logs calibre-server`; server port is 8081 |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[traefik]] (`proxy` network) | no `calibre.kmkdp.com` ingress / TLS |
| Needs | `/mnt/media/books/ebook` (Chaptarr output, `:ro`) | nothing to import |
| Needed by | reMarkable / KOReader | no OPDS pull source |

## Observability

No dedicated dashboard. Container health via Komodo / `docker ps`; logs ship to
Loki via the observability agent.

## Architecture

Two services from one locally-built image (`kdk/calibre`): `calibre-server` runs
`calibre-server --port 8081` (web reader + OPDS at `/opds`) on both `default` and
the external `proxy` network, published on `10.1.20.20:8081`; `calibre-import`
runs a loop that `calibredb add --automerge ignore` every `INTERVAL` (900 s) from
`/mnt/media/books/ebook` (mounted `:ro`) into the library, de-duping by
title/author. Only `server` builds the image (`import` reuses the tag) to avoid
two image IDs recreating each other on every deploy. Default network pinned to
`10.208.22.0/24` (see [docker-ipam](../reference/docker-ipam.md)).

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/calibre/compose.yaml` | the two services + Traefik labels (no `forwardauth`) |
| `/opt/kdk-lab/calibre/library` | the Calibre library (`metadata.db` + books) |
| `/mnt/media/books/ebook` | Chaptarr's ebook output, the import source (`:ro`) |
| Komodo stack `calibre` | server kdk-dkr-01, `run_build = true`, webhook on |

## Secret Rotation

No secrets — the server has no auth (LAN/Tailscale is the gate) and the image is
built locally.

## User Guide

Add the OPDS catalog `https://calibre.kmkdp.com/opds` in KOReader (on the LAN or
over Tailscale). Books that Chaptarr downloads into `/mnt/media/books/ebook` are
imported automatically within 15 minutes.
