---
title: Immich
host: kdk-dkr-dmz-01
stack: immich
tier: dmz
status: live
storage: yes
secrets: [immich-postgres]
updated: 2026-09-12
tags: [homelab/stack, tier/dmz, status/live]
---

# Immich

Photo and video backup, public via the Cloudflare tunnel; a fresh deploy (the k3s instance was unused), segmented on its own internal network.

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Self-hosted photo backup with ML search |
| Status | 🟢 Live — web loads; mobile app reaches it off-LAN |
| Host | [[kdk-dkr-dmz-01]] |
| Endpoint | `https://immich.kmkdp.com` |
| Images | server + ML `v3.0.0`, valkey `9`, postgres `14-vectorchord` (digest-pinned) |
| Deploy | Komodo stack `immich` (git-linked, `op inject` pre_deploy) |
| Storage | library `/mnt/appdata/immich` (NFS); postgres `/opt/kdk-lab/immich/postgres` (VM disk, nightly dump) |
| Blast radius | No photo access/upload; nothing else affected (segmented) |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://immich.kmkdp.com/api/server/ping   # 200
docker exec immich-postgres pg_isready -U immich                                     # accepting
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Server won't start | Postgres image mismatch (vectorchord/pgvecto.rs) | keep the digest-pinned `immich-app/postgres` image |
| ML search errors | ML container can't reach Hugging Face | `internal` network has egress; check `docker logs immich-ml` |
| Uploads fail | library NFS not mounted | `mountpoint /mnt/appdata` on the host |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[dmz-proxy]] + tunnel | ingress + public access |
| Needs | `/mnt/appdata/immich` (NFS) | no library storage |
| Needed by | — | none |

## Observability

No dashboard. `immich.kmkdp.com` probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

`immich-server:2283` joins both `internal` and `proxy-dmz`; ML, valkey, and postgres stay on `internal` only. ML keeps egress to Hugging Face for model downloads; no inbound is published except through the server.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/immich/compose.yaml` | Four-service segmented stack |
| `DB_*` / `REDIS_HOSTNAME` | internal service names |
| `/opt/kdk-lab/immich/model-cache` | ML model cache |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `immich-postgres` | DB auth (server + ML + dump) | 🟢 self-service | rotate DB + item together, redeploy |

## User Guide

First visit prompts admin creation. Users install the Immich mobile app, point it at `immich.kmkdp.com`, and enable background backup.
