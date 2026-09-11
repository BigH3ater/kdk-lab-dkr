---
title: Komodo
---
# Komodo

Deployment control plane and the lab's single pane of glass.

| | |
|---|---|
| Host | kdk-dkr-01 (10.1.20.20) |
| URL | https://komodo.kmkdp.com — local login `kdkadmin` (1P: `komodo-admin`) |
| Stack | `stacks/komodo` — core 2.3.3 + FerretDB 2 + postgres-documentdb 17 |
| Data | /opt/kdk-lab/komodo (VM disk) |
| Secrets | komodo-db, komodo-passkey, komodo-jwt-secret, komodo-webhook-secret, komodo-admin (kdk-cluster) |
| Deploys | Resource Sync `kdk-lab-dkr` reads `komodo/resources.toml` from main |

Verify: `curl -s https://komodo.kmkdp.com/version` → 2.3.3; all servers green.

Gotcha: core and periphery must track the same release line; ghcr's `latest`
tag lags badly (pulled 1.19.5 when current was 2.3.3). Pin both.
