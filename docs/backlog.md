---
title: Backlog & TODOs
tags: [homelab/backlog, status/planned]
updated: 2026-09-15
---

# Backlog & TODOs

Tracked enhancements not yet implemented. Each item states the problem, the
goal, and a sketch of the approach so it's actionable later.

## Enable Komodo Managed Mode (bidirectional sync)

**Status:** 🟡 blocked (needs a GitHub write token) · **Filed:** 2026-09-14

Let Komodo write UI changes back to `komodo/resources.toml` and commit them, so
the file is Komodo-generated rather than hand-authored, and enable
`delete`/prune of resources that lose the `kdk-lab` tag.

**Blocked on:** a GitHub **write** PAT (scope `repo` on `BigH3ater/kdk-lab-dkr`).
None exists in `kdk-ops`/`kdk-cluster` and it can't be minted from here — create
one and store it (e.g. `op://kdk-ops/github-kdk-lab-dkr-rw`), then:

1. Add a `[[git_provider]]` with that account to Komodo Core (`core.config.toml`)
   and restart Core.
2. Set the sync's `git_account` to that user and enable Managed Mode.
3. Enable `delete` **only after** confirming every non-built-in resource carries
   the `kdk-lab` tag (the `gitops-reconcile` action tags stragglers) — otherwise
   the built-in "Backup Core Database" / "Global Auto Update" procedures would be
   pruned. See [[komodo-gitops]].

---

## Recently completed

- **AdGuard DNS from Traefik labels (2026-09-15).** DNS is now derived from each
  stack's compose `Host()` label + target server and reconciled into AdGuard by
  the `dns-reconcile` one-shot, run by the `gitops-sync` procedure's **dns**
  stage every 15 min. Add/updates service records and prunes stale ones (the
  dead k3s VIP and removed internal/DMZ services); Pi-tier adopted services and
  host FQDNs are left alone. See [[komodo-gitops]].
