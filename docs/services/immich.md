---
title: Immich
host: kdk-dkr-dmz-01
stack: immich
tier: dmz
status: live
storage: yes
secrets: [immich-postgres]
updated: 2026-11-16
tags: [homelab/stack, tier/dmz, status/live]
---

# Immich

Photo and video backup, LAN/tailnet only (router on the DMZ Traefik's `websecure` entrypoint only, not the Cloudflare tunnel; `IMMICH_TRUSTED_PROXIES=10.211.2.3/32` = that Traefik — see [[dmz-proxy]]); a fresh deploy (the k3s instance was unused), segmented on its own internal network.

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Self-hosted photo backup with ML search |
| Status | 🟢 Live — web loads; mobile app reaches it off-LAN |
| Host | [[kdk-dkr-dmz-01]] |
| Endpoint | `https://immich.kmkdp.com` |
| Images | server + ML `v3.4.1`, valkey `9`, postgres `14-vectorchord` (digest-pinned) |
| Runtime users | server + ML `1000:1000`, postgres + valkey `999:999` (all non-root, see [Hardening](#hardening)) |
| Deploy | Komodo stack `immich` (git-linked; pre_deploy chowns the library + model cache to 1000:1000, then `op inject`) |
| Storage | library `/mnt/appdata/immich` (NFS); postgres `/opt/kdk-lab/immich/postgres` (VM disk, nightly dump) |
| Blast radius | No photo access/upload; nothing else affected (segmented) |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://immich.kmkdp.com/api/server/ping   # 200
ssh kdkadmin@192.168.191.20 'sudo docker exec immich-postgres pg_isready -U immich'               # accepting
ssh kdkadmin@192.168.191.20 'sudo docker ps --filter name=immich --format "{{.Names}} {{.Status}}"'   # 4x (healthy)
ssh kdkadmin@192.168.191.20 'sudo docker exec immich-server id -u; sudo docker exec immich-ml id -u'  # 1000, 1000
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Server won't start | Postgres image mismatch (vectorchord/pgvecto.rs) | keep the digest-pinned `immich-app/postgres` image |
| ML search errors | ML container can't reach Hugging Face | `internal` network has egress; check `docker logs immich-ml` |
| Uploads fail | library NFS not mounted | `mountpoint /mnt/appdata` on the host |
| Uploads / thumbnails fail with `EACCES` | a file under the library not owned by 1000 (copied in by root) | `sudo find /mnt/appdata/immich ! -user 1000 \| head`; redeploy (pre_deploy re-chowns) |
| ML `Permission denied` on `/cache` | model cache not 1000:1000 | `stat -c %u:%g /opt/kdk-lab/immich/model-cache`; redeploy |
| `Read-only file system` in a log | the image started writing a new path outside the tmpfs list | add that path as a `tmpfs` (or a skip label with the reason) in `compose.yaml` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[dmz-proxy]] | ingress (LAN/tailnet) |
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
| `/opt/kdk-lab/immich/model-cache` | ML model cache (owned 1000:1000) |
| `/opt/kdk-lab/immich/postgres` | PGDATA (owned 999, the image's postgres user) |
| `komodo/resources.toml` stack `immich` | pre_deploy: idempotent `find ... -exec chown 1000:1000` on the library + model cache, then `op inject` |

## Hardening

Meets the [compose baseline](../reference/compose-baseline.md) with no skip or exception
labels (`x-hardened` anchor: `no-new-privileges`, `cap_drop: [ALL]`, `read_only`, `pids_limit`,
`mem_limit`, json-file 10m x 3, explicit healthcheck). Applied 2026-11-16, while the
instance was empty (1 admin, 0 assets).

| Service | User | Writable paths | Limits (mem / pids) | Healthcheck |
|---|---|---|---|---|
| `immich-server` | `1000:1000` | `/data` (library NFS), `/tmp` tmpfs (`HOME=/tmp`) | 4g / 1024 | `immich-healthcheck` (`/api/server/ping`) |
| `immich-machine-learning` | `1000:1000` | `/cache` (model cache), `/tmp` tmpfs (`HOME=/tmp`) | 4g / 512 | `python3 healthcheck.py` (`:3003/ping`) |
| `immich-valkey` | `999:999` | `/data` tmpfs (64m), `/tmp` | 256m / 128 | `valkey-cli ping` |
| `immich-postgres` | `999:999` | PGDATA bind; tmpfs `/etc/postgresql`, `/run/postgresql`, `/tmp` | 2g / 256 | image `healthcheck.sh` (pg_isready + checksum failures), every 30s |

- No capabilities are added for server, ML or valkey (unprivileged ports, no setuid).
  Postgres keeps `cap_add: [CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER]`, a no-op while it
  runs as 999; it is there so a rollback that deletes only `user:` still starts.
- `/etc/postgresql` is a tmpfs because the image entrypoint copies the SSD config template
  there on every start. Valkey's `/data` is a tmpfs because the image snapshots `dump.rdb`
  into its workdir; it only holds job queues, so a restart drops queued jobs (re-run them
  from Administration -> Jobs). A recreate already did that before hardening.
- The server waits for postgres and valkey to be healthy (`depends_on: service_healthy`).
- The appdata NFS export (`172.16.30.2:/mnt/tankz3/appdata`) does not squash root, so the
  pre_deploy chown works. If it ever squashes root, the chown fails and the deploy stops.
- Service/container `immich-postgres` and the `immich_internal` network names are fixed:
  the `backup-dmz` stack's `pgdump-immich` ([[backups]]) joins that network to dump the DB.

**Rollback:** revert the PR and redeploy. The 1000:1000 ownership left behind is harmless
for a root-run container.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `immich-postgres` | DB auth (server + ML + dump) | 🟢 self-service | rotate DB + item together, redeploy |

## User Guide

First visit prompts admin creation. Users install the Immich mobile app, point it at `immich.kmkdp.com`, and enable background backup.
