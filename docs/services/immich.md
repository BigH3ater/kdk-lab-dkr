---
title: Immich
---
# Immich

Photo backup, public via the tunnel. Fresh deploy 2026-09-11 (the k3s
instance was unused) — segmented on its own docker network.

| | |
|---|---|
| Host | kdk-dkr-dmz-01 |
| URL | https://immich.kmkdp.com |
| Stack | `stacks/immich` — server+ML v3.0.0, valkey 9, vectorchord postgres |
| Data | library /mnt/appdata/immich (NFS tankz3); postgres /opt/kdk-lab/immich (VM disk, nightly dump) |
| Secrets | immich-postgres (kdk-cluster) |

Verify: web loads, create admin on first visit, mobile app reaches it off-LAN.
