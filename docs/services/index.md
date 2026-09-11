---
title: Services
---
# Services

Stack definitions live in `stacks/` in the repo; Komodo deploys them from git.

| Service | Host | URL | Stack |
|---|---|---|---|
| [Komodo](komodo.md) | kdk-dkr-01 | https://komodo.kmkdp.com | komodo |
| [Traefik internal](traefik.md) | kdk-dkr-01 | — | proxy |
| [Docs](docs.md) | kdk-dkr-01 | https://docs.kmkdp.com | docs |
| [1Password Connect](op-connect.md) | kdk-dkr-01 | 10.1.20.20:8080 (token) | op-connect |
| [Identity](identity.md) | kdk-dkr-dmz-01 | https://sso.kmkdp.com | identity |
| [Jellyfin](jellyfin.md) | kdk-dkr-dmz-01 | https://jellyfin.kmkdp.com | jellyfin |
| [Immich](immich.md) | kdk-dkr-dmz-01 | https://immich.kmkdp.com | immich |
| [Seerr](seerr.md) | kdk-dkr-dmz-01 | https://seerr.kmkdp.com | dmz-apps |
| [Media stack](media.md) | kdk-dkr-01 | per-app | media |
| [Apps](apps.md) | kdk-dkr-01 | various | apps |
| [Backups](backups.md) | both | — | backup-* |
