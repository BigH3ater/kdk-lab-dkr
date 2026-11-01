---
title: Services
---
# Services

Stack definitions live in `stacks/` in the repo; Komodo deploys them from git. One page per stack.

| Service | Host | URL | Stack |
|---|---|---|---|
| [Komodo](komodo.md) | kdk-dkr-01 | https://komodo.kmkdp.com | komodo |
| [Traefik internal](traefik.md) | kdk-dkr-01 | — | proxy |
| [Docs](docs.md) | kdk-dkr-01 | https://docs.kmkdp.com | docs |
| [1Password Connect](op-connect.md) | kdk-dkr-01 | 10.1.20.20:8080 (token) | op-connect |
| [Media stack](media.md) | kdk-dkr-01 | per-app | media |
| [Apps](apps.md) | kdk-dkr-01 | various | apps |
| [Calibre](calibre.md) | kdk-dkr-01 | https://calibre.kmkdp.com | calibre |
| [Obsidian to reMarkable](obsidian-to-koreader.md) | kdk-dkr-01 | — (SSH to tablet) | obsidian-to-koreader |
| [Trailarr](trailarr.md) | kdk-dkr-01 | https://trailarr.kmkdp.com | trailarr |
| [net-exporters](net-exporters.md) | kdk-dkr-01 | — (scraped by kdk-mon-01) | net-exporters |
| [grocery-planner](grocery-planner.md) | kdk-dkr-01 | home.kmkdp.com/meal/groceries (one-shots: weekly/poll/bulk) | grocery-planner |
| [Traefik DMZ + tunnel](dmz-proxy.md) | kdk-dkr-dmz-01 | — | dmz-proxy |
| [Identity](identity.md) | kdk-dkr-dmz-01 | https://sso.kmkdp.com | identity |
| [Jellyfin](jellyfin.md) | kdk-dkr-dmz-01 | https://jellyfin.kmkdp.com | jellyfin |
| [Audiobookshelf](audiobookshelf.md) | kdk-dkr-dmz-01 | https://audiobookshelf.kmkdp.com | audiobookshelf |
| [Immich](immich.md) | kdk-dkr-dmz-01 | https://immich.kmkdp.com | immich |
| [Tandoor](tandoor.md) | kdk-dkr-dmz-01 | https://recipes.kmkdp.com | tandoor |
| [Seerr](seerr.md) | kdk-dkr-dmz-01 | https://seerr.kmkdp.com | dmz-apps |
| [Wizarr](wizarr.md) | kdk-dkr-dmz-01 | https://wizarr.kmkdp.com | wizarr |
| [Backups](backups.md) | both VMs | — | backup-* |
| [Observability](observability.md) | kdk-mon-01 + kdk-log-01 | https://grafana.kmkdp.com (fallback http://10.1.20.30:3000) | monitoring, loki, observability-agent |
| [advisory-watch](advisory-watch.md) | kdk-log-01 | — (weekly, Grafana → Security) | advisory-watch |
| [cloudflare-watch](cloudflare-watch.md) | kdk-log-01 | — (hourly + weekly, Grafana → Loki) | cloudflare-watch |
| [trivy-scan](trivy-scan.md) | all compose hosts | — (weekly, Grafana → Security) | trivy-scan |
| [NUT (UPS)](nut.md) | kdk-dkr-03 | 10.1.30.22:3493 (NUT; 10.1.3.62 on the UPS L2) | nut |
| [Home Assistant](home-assistant.md) | kdk-dkr-02 | http://10.1.30.21:8123 | home-assistant |

Formerly adopted (files-on-host) stacks, now git-linked Komodo stacks deployed from the repo (`repo` + `run_directory` in `komodo/resources.toml`): [Home Assistant](home-assistant.md) on [kdk-dkr-02](../hosts/kdk-dkr-02.md), Scrypted on [kdk-dkr-03](../hosts/kdk-dkr-03.md), monitoring (`monitoring-ext`) on [kdk-mon-01](../hosts/kdk-mon-01.md). Only host-managed secrets and a little host-rendered config still live on those boards.

Every doc follows the kdk-lab template: **At a glance · Verify · Troubleshooting · Dependencies · Observability · Architecture · Configuration · Secret Rotation · User Guide**.
