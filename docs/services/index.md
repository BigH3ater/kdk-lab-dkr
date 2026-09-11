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
