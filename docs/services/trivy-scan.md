---
title: trivy-scan — weekly image vulnerability scan
host: all compose hosts
stack: trivy-scan
tier: infra
status: live
storage: yes
secrets: []
updated: 2026-10-30
tags: [homelab/stack, tier/infra, status/live, security]
---

# trivy-scan — weekly image vulnerability scan

Every Monday each compose host scans the images of its **running** containers with Trivy and reports one line per image (HIGH/CRITICAL counts, how many are fixable, the most urgent CVE) to Loki. Complements [[advisory-watch]]: that one reads upstream advisories for the app, this one finds vulnerable OS and library packages inside the image.

**Related:** [[observability]] · [[advisory-watch]] · [security alerts runbook](../runbooks/security-alerts.md#advisories)

## At a glance

| Field | Value |
|---|---|
| Runs | Monday 06:03 America/Chicago, procedure `trivy-scan` → stacks `trivy-scan-{dkr01,dmz01,mon01,dkr02,dkr03,log01}` (one stage, parallel; deploy=false, destroy_before_deploy) |
| Services | `socket-proxy` (`tecnativa/docker-socket-proxy:v0.5.0`), `scan` (`aquasec/trivy:0.76.1`), `report` (`python:3.12.16-alpine`) |
| Host access | GET-only Docker API through the proxy (CONTAINERS, IMAGES, INFO, PING, VERSION; POST=0) on an `internal` network; the proxy exits when `report` has finished |
| Output | Loki `{job="trivy", host="<host>"}`: `image image_id status critical high fixable_critical fixable_high top_cve top_pkg top_fixed`, plus a `summary=1` line |
| Alert | `TrivyFixableCritical` (fixable_critical > 0), `TrivyScanSilent` |
| Volumes | `trivy-cache` (vulnerability DB, ~1 GB), `trivy-out` (this run's JSON) |
| Blast radius | read-only for the host: nothing can start, stop or exec a container (so the NUT host kdk-dkr-03 is scanned too) |

## Verify

```bash
# Komodo -> Procedures -> trivy-scan -> Run, then on any host:
sudo docker logs trivy-scan-report 2>&1 | tail -2        # summary=1 images=N errors=0 ... scan_exit=0
sudo docker ps -a --filter name=trivy-scan --format '{{.Names}} {{.Status}}'   # all three Exited (0)
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| `status=error` for an image | trivy failed for that image (timeout, export error) | `docker run --rm -v trivy-scan_trivy-out:/o alpine:3.20.10 cat /o/<n>.log` |
| `scan_exit` non-zero / report exit 1 | the scan container failed (DB download, proxy unreachable) | `docker logs trivy-scan` |
| Pull denied on kdk-mon-01 | Docker Hub rate limit on the Pi | `docker pull aquasec/trivy:0.76.1` by hand once |
| Disk fills during the run | trivy exports each image under `/cache/tmp` (volume) | `docker system df -v \| grep trivy` |
| Proxy still running | `report` never started; proxy stops itself after 3 h | `docker logs trivy-scan-proxy` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | egress to `mirror.gcr.io` / `ghcr.io` (trivy-db), Loki `10.1.20.21:3100` | no DB / no output (Pushover fallback) |
| Needed by | weekly review, `TrivyFixableCritical` | image CVEs go unseen |

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/trivy-scan/scan.sh` | lists running images via the proxy, `trivy image --image-src docker --scanners vuln --severity HIGH,CRITICAL` (no `--ignore-unfixed`: totals and fixable counts both reported) |
| `stacks/trivy-scan/report.py` | summariser, Loki push, Pushover only if the push fails (`PUSHOVER_MODE=always` to always send) |
| `stacks/trivy-scan/proxy.sh` | runs haproxy, exits after `trivy-scan-report` exits |
| Komodo `environment` | `NODE_NAME`, `LOKI_URL` per host |

## Secret Rotation

None. Pushover creds come from `/etc/kdk/pushover.env` (lab-wide, see [[observability]]).

## User Guide

Grafana → Security → Security Advisories, second table. `fixable_critical > 0` means a fixed package exists: bump the image (or rebuild a local `kdk/*` image on a newer base). Unfixable criticals stay visible in `critical` but never page.
