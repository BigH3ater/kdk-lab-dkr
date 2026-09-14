---
title: Backlog & TODOs
tags: [homelab/backlog, status/planned]
updated: 2026-09-14
---

# Backlog & TODOs

Tracked enhancements not yet implemented. Each item states the problem, the
goal, and a sketch of the approach so it's actionable later.

## Automate AdGuard DNS records from Traefik labels / Komodo

**Status:** 🔵 planned · **Priority:** medium · **Filed:** 2026-09-14

**Problem.** AdGuard DNS rewrites are maintained by hand via
`scripts/adguard-rewrite.sh` against the origin (`kdk-dns-01`,
`adguardhome-sync` propagates to dns-02/03). This drifts from reality — the
2026-09-14 audit found a missing record (`dockge`), three stale k3s records
(`argocd`, `grafana`, `grafana-embed` → dead `10.1.20.10`), and two shifted
host-FQDN records (`kdk-dkr-01`/`-02`). Every new stack needs a manual DNS
step that is easy to forget.

**Goal.** When a stack is deployed, its `*.kmkdp.com` hostnames should be
created/updated in AdGuard automatically, pointing at the correct Traefik
ingress for that host's tier — so DNS is derived from the compose files, not
maintained separately.

**Ingress map (host tier → answer):**

| Tier | Traefik host | Answer IP |
|---|---|---|
| Internal | kdk-dkr-01 | `10.1.20.20` (also the `*.kmkdp.com` wildcard) |
| DMZ | kdk-dkr-dmz-01 | `192.168.191.20` |
| Pi | kdk-dkr-02 | `10.1.30.21` |

**Approach (sketch).**
1. Source of truth = the `traefik.http.routers.<name>.rule: Host(\`x.kmkdp.com\`)`
   labels already in each stack's `compose.yaml` (parse the repo, or read live
   container labels via the Docker socket).
2. Map each Host() to the ingress IP of the server the stack runs on
   (Komodo already knows stack→server).
3. Reconcile into AdGuard on the origin via the existing `rewrite/add` /
   `rewrite/update` API (`adguard-rewrite.sh` logic); `adguardhome-sync` then
   propagates to dns-02/03.
4. Trigger options: a Komodo **Action/Procedure** run post-deploy, or extend
   the per-host `observability-agent` docker-event-watch to reconcile on
   container start, or a small periodic reconciler (compare compose Host()
   set vs AdGuard, add/fix/prune).
5. Only manage records under a known-owned set (leave hand-made infra records
   like `pve`, `truenas`, host FQDNs alone, or bring those under the same map).

**Notes.** The wildcard `*.kmkdp.com → 10.1.20.20` already covers internal
services; explicit records are only needed for the DMZ and Pi tiers (and to
override the wildcard). Automation should focus on those.

## Enable Komodo Managed Mode (bidirectional sync)

**Status:** 🔵 planned · **Filed:** 2026-09-14

Let Komodo write UI changes back to `komodo/resources.toml` and commit them, so
the file is Komodo-generated rather than hand-authored. Requires a GitHub
**write** token (PAT with `repo` scope for `BigH3ater/kdk-lab-dkr`) configured as
a Komodo git provider (`[[git_provider]]` in `core.config.toml`), the sync's
`git_account` set to that user, and Managed Mode enabled. See [[komodo-gitops]].
Also enables `delete`/prune once the `kdk-lab` tag is confirmed ingested.
