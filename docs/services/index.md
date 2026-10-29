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
| [Traefik DMZ + tunnel](dmz-proxy.md) | kdk-dkr-dmz-01 | — | dmz-proxy |
| [Identity](identity.md) | kdk-dkr-dmz-01 | https://sso.kmkdp.com | identity |
| [Jellyfin](jellyfin.md) | kdk-dkr-dmz-01 | https://jellyfin.kmkdp.com | jellyfin |
| [Audiobookshelf](audiobookshelf.md) | kdk-dkr-dmz-01 | https://audiobookshelf.kmkdp.com | audiobookshelf |
| [Immich](immich.md) | kdk-dkr-dmz-01 | https://immich.kmkdp.com | immich |
| [Seerr](seerr.md) | kdk-dkr-dmz-01 | https://seerr.kmkdp.com | dmz-apps |
| [Wizarr](wizarr.md) | kdk-dkr-dmz-01 | https://wizarr.kmkdp.com | wizarr |
| [Backups](backups.md) | both VMs | — | backup-* |
| [Observability](observability.md) | kdk-mon-01 + kdk-log-01 | https://grafana.kmkdp.com (fallback http://10.1.20.30:3000) | monitoring, loki, observability-agent |
| [advisory-watch](advisory-watch.md) | kdk-log-01 | — (weekly, Grafana → Security) | advisory-watch |
| [trivy-scan](trivy-scan.md) | all compose hosts | — (weekly, Grafana → Security) | trivy-scan |
| [NUT (UPS)](nut.md) | kdk-dkr-03 | 10.1.30.22:3493 (NUT; 10.1.3.62 on the UPS L2) | nut |
| [Home Assistant](home-assistant.md) | kdk-dkr-02 | http://10.1.30.21:8123 | home-assistant (adopted) |

Adopted stacks (files on the host, managed in Komodo): [Home Assistant](home-assistant.md) on [kdk-dkr-02](../hosts/kdk-dkr-02.md), Scrypted on [kdk-dkr-03](../hosts/kdk-dkr-03.md), monitoring on [kdk-mon-01](../hosts/kdk-mon-01.md).

Every doc follows the kdk-lab template: **At a glance · Verify · Troubleshooting · Dependencies · Observability · Architecture · Configuration · Secret Rotation · User Guide**.
