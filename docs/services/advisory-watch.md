---
title: advisory-watch — upstream advisories vs pinned images
host: kdk-log-01
stack: advisory-watch
tier: infra
status: live
storage: yes
secrets: [github-advisory-ro]
updated: 2026-10-28
tags: [homelab/stack, tier/infra, status/new, security]
---

# advisory-watch — upstream advisories vs pinned images

Weekly one-shot that compares every image tag pinned in `stacks/` with what its upstream published (GitHub repository advisories, OSV, NVD, RSS) and writes one Loki line per advisory with a verdict. It answers "is anything we run affected by a new advisory?" without waiting for Renovate.

**Related:** [[observability]] · [[trivy-scan]] · [[kdk-log-01]] · [security alerts runbook](../runbooks/security-alerts.md#advisories)

## At a glance

| Field | Value |
|---|---|
| Runs | Monday 07:03 America/Chicago, procedure `advisory-watch` → stack `advisory-watch` (deploy=false, destroy_before_deploy) on [[kdk-log-01]] |
| Image | `python:3.12.16-alpine`, uid 1000, read-only, no caps, 256 MB |
| Input | the repo clone (`../../` → `/repo:ro`), `stacks/advisory-watch/catalog.json` (81 image repositories, 13 marked `confirm`) |
| Output | Loki `{job="advisory-watch"}` (`image tag severity id verdict fixed_in source stacks url title`), stdout |
| State | `/opt/kdk-lab/advisory-watch/state.json` (`{source_id: last_success}`) |
| Alerts | `AdvisoryPinnedImageVulnerable` (warning), `AdvisoryNeedsReview` (info), `AdvisoryWatchSilent` |
| Blast radius | none: reads public APIs, writes only its state dir and Loki |

## Verify

```bash
# Komodo -> Procedures -> advisory-watch -> Run, then:
ssh kdkadmin@10.1.20.21 'sudo docker logs advisory-watch 2>&1 | tail -3'     # ... summary=1 images_checked=N ... source_errors=0
curl -sG http://10.1.20.21:3100/loki/api/v1/query_range --data-urlencode 'query={job="advisory-watch"} |= "summary=1"' --data-urlencode 'since=24h' | head -c 400
# Local dry run (no push, no state):
python3 stacks/advisory-watch/advisory_watch.py --repo . --state /tmp/aw.json --dry-run --limit 3
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Run takes ~1 h | no `GITHUB_TOKEN`: the job sleeps through GitHub's 60 req/h window | `docker logs advisory-watch \| grep "rate limited"`; add the token (Secret Rotation) |
| Exit 1 | one source failed (others' state saved, the failed one retries next week) | `docker logs advisory-watch \| grep level=error` |
| Exit 2 | Loki push failed; Pushover fallback sent, nothing saved | `curl -s http://10.1.20.21:3100/ready` |
| CI `advisory-catalog` fails | a new image in `stacks/` has no catalog entry | `python3 scripts/advisory-catalog-check.py` |
| Too many `Unknown` | floating/`latest` tags or wrapper images | pin the tag; see `stacks/advisory-watch/README.md` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | Loki on kdk-log-01, internet egress (api.github.com, api.osv.dev, services.nvd.nist.gov, feeds) | no output / source errors |
| Needed by | weekly review (Grafana Security Advisories) | upstream advisories go unseen until Renovate |

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/advisory-watch/catalog.json` | image repository → `github` / `osv` / `nvd_cpe` / `feed`, `tag_is_version`, `confirm`, `note` |
| `FIRST_RUN_DAYS` (30) | look-back for a source with no state |
| `NVD_API_KEY`, `NVD_DISABLE=1` | optional NVD key / switch NVD off |
| `.env.tpl` | `GITHUB_TOKEN=op://kdk-ops/github-advisory-ro/credential`, rendered only where `/etc/komodo/op.env` exists |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `kdk-ops/github-advisory-ro` (optional) | GitHub API rate limit only (fine-grained token, public repositories, no permissions) | 🟢 low | yes: `op item edit github-advisory-ro --vault kdk-ops credential=<new token>`; next run picks it up |

## User Guide

Open Grafana → Security → Security Advisories on Monday after 07:30. Each non-Patched row is one decision: bump the image (merge the Renovate PR or change the pin), accept it, or fix the catalog mapping. How to add a catalog entry: `stacks/advisory-watch/README.md`.
