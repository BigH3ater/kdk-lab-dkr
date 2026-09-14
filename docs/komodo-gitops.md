---
title: Komodo GitOps (Resource Sync)
tags: [homelab/process, komodo, gitops]
updated: 2026-09-14
---

# Komodo GitOps — Resource Sync

How this repo drives Komodo. Every Docker stack on every host is declared in
`komodo/resources.toml` and cloned/deployed from this repo by Komodo. There is
no hand-managed Docker outside Komodo.

**Related:** [[backlog]] · `komodo/resources.toml`

## The moving parts

| Piece | What it is | Clone path |
|---|---|---|
| **Repo `kdk-lab-dkr`** | The single definition of the git source (`BigH3ater/kdk-lab-dkr@main`). The sync reads through it via `linked_repo`. Declared in `resources.toml` itself. | Core repo-cache (internal) |
| **ResourceSync `kdk-lab-dkr`** | Core-level resource. `linked_repo` → the Repo above; reads `komodo/resources.toml` and creates/updates every server, stack, procedure and action. The one resource that can't declare itself (self-reference fails validation) — maintained in the UI. | none — Komodo **Core** clones internally |
| **Procedure `gitops-sync`** | The automation loop, every 15 min. Stage `prep`: run Action `gitops-reconcile`. Stage `sync`: `RunSync kdk-lab-dkr`. RunSync must live in the procedure, not the action — the sync updates the action's own config, and Komodo refuses to update a mid-run resource ("Action busy"), so an action awaiting its own RunSync can never receive updates to itself. The stage barrier guarantees the action has exited first. Needed because Core is LAN-only, so GitHub push webhooks can't reach it. | runs on Core |
| **Action `gitops-reconcile`** | Stage-`prep` logic (Deno): enforce `kdk-lab` tags on every non-built-in resource (the sync only matches tagged resources, see below), then `RefreshResourceSyncPending` (pulls the repo, recomputes pending). | runs on Core |
| **Git-linked stacks** | The actual Docker. Each `[[stack]]` has `repo = BigH3ater/kdk-lab-dkr` + `run_directory = stacks/<name>`. | on its periphery host at **`/opt/kdk-lab/stacks/<name>/`** (periphery `stack_dir`; `/etc/komodo/stacks/<name>/` on hosts with the older periphery default, e.g. kdk-mon-01 and the DMZ VM) |

Note the toml is **read** by the sync after the pull — it is never *generated*
by the pull/clone job. The only supported way for Komodo to write the toml is
Managed Mode (commit-back), which needs a GitHub write token (see below).

## Tags — the safety scope

Every managed resource carries `tags = ["kdk-lab"]`, and the sync is set to
`match_tags = ["kdk-lab"]`. This scopes **managed / delete-unmatched** to only
`kdk-lab`-tagged resources, so Komodo's own built-in procedures
(`Backup Core Database`, `Global Auto Update`) and anything created ad-hoc are
never pruned.

> ⚠️ **The sync only applies tags when it *creates* a resource — and it only
> *matches* resources that already carry `match_tags`.** An untagged
> pre-existing resource is invisible to the sync: it tries to re-create it
> (name-conflict error) and runs "deploy on creation" against the live stack.
> That is why `gitops-reconcile` stamps `kdk-lab` on every non-built-in
> resource *before* it refreshes/runs the sync. `delete` (prune unmatched)
> stays safe under this: it is scoped to `kdk-lab`-tagged resources, and the
> two built-ins are never tagged. Enable `delete` only after confirming every
> declared resource carries the tag (the action's log shows what it tagged).

## The process — push → reconcile → deploy

1. Push to `main`.
2. Within 15 min the `gitops-sync` Procedure runs: the `gitops-reconcile`
   Action enforces tags and refreshes the sync (Core pulls the linked repo),
   then the procedure executes `RunSync`.
3. `RunSync` reads `komodo/resources.toml` and reconciles Komodo to it
   (create/update; delete only if `delete` is enabled and the resource is
   `kdk-lab`-tagged). Stacks with `deploy = true` whose config changed are
   redeployed in `after`-order.
4. Each git-linked stack, on deploy, pulls its clone on its host and runs
   `docker compose up`.

Manual path: Execute the `gitops-sync` Procedure (Komodo → Procedures), or
Komodo → **Syncs** → `kdk-lab-dkr` → Execute → Run Sync.

Komodo **refuses to apply a toml with validation errors** ("Found file errors.
Cannot execute sync.") — a good guard. Common causes when hand-editing:
declaring Komodo's built-in procedures, or a `[[resource_sync]]` that references
itself. Keep those out of the file.

## Add or change a stack

1. Add `stacks/<name>/compose.yaml` (secrets via `.env.tpl` + `op inject` in a
   `pre_deploy` command, like the existing stacks).
2. Declare it in `komodo/resources.toml`:
   ```toml
   [[stack]]
   tags = ["kdk-lab"]
   name = "<name>"
   [stack.config]
   server = "<server>"          # kdk-dkr-01 | kdk-dkr-dmz-01 | kdk-dkr-02 | kdk-dkr-03 | kdk-mon-01
   repo = "BigH3ater/kdk-lab-dkr"
   run_directory = "stacks/<name>"
   webhook_enabled = true
   ```
3. Commit + push to `main`.
4. Done — within 15 min `gitops-sync` applies it (creates the stack tagged
   `kdk-lab`; `deploy = true` stacks deploy in `after`-order). To apply
   immediately, Execute the `gitops-sync` Procedure in the UI instead.

## Target state — Managed Mode (bidirectional)

Komodo can also write UI changes **back** to `resources.toml` and commit them
(Managed Mode), so the repo need not be hand-authored. This requires a GitHub
**write token** configured as a git provider in Komodo:

```toml
# core.config.toml
[[git_provider]]
domain = "github.com"
accounts = [{ username = "<gh-user>", token = "ghp_..." }]
```

…then set the sync's `git_account` to that user and enable Managed Mode.
**Not yet set up** — no write token exists (`git_providers: []`). Tracked in
[[backlog]]. Until then, the repo is authored here and Komodo syncs it read-only.

## Current sync config

- Source: `linked_repo` → Repo `kdk-lab-dkr` (`BigH3ater/kdk-lab-dkr` @ `main`),
  `resource_path = ["komodo/resources.toml"]`
- `managed = true`, `delete = false` (enable `delete` only after tag ingestion), `match_tags = ["kdk-lab"]`
- Declares: 5 servers, 1 repo, 24 stacks, 3 tagged procedures (`nightly-backups`,
  `publish-docs`, `gitops-sync`), 1 action (`gitops-reconcile`). The 2 built-in procedures stay
  untagged/unmanaged.
