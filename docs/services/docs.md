---
title: Docs site
---
# Docs site

This site: `docs/` in the repo, built with Quartz, served by nginx.

| | |
|---|---|
| Host | kdk-dkr-01 |
| URL | https://docs.kmkdp.com (ForwardAuth-gated) |
| Stack | `stacks/docs` — one-shot builder (node 24 + Quartz) + nginx |
| Rebuild | push to main → Komodo webhook redeploys → builder reruns |

Verify: `curl -s https://docs.kmkdp.com/healthz` → ok.
