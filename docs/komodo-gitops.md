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

## The two moving parts

| Piece | What it is | Clone path |
|---|---|---|
| **ResourceSync `kdk-lab-dkr`** | Core-level resource. Reads `komodo/resources.toml` and creates/updates every server, stack and procedure. | none — Komodo **Core** clones internally |
| **Git-linked stacks** | The actual Docker. Each `[[stack]]` has `repo = BigH3ater/kdk-lab-dkr` + `run_directory = stacks/<name>`. | on its periphery host at **`/opt/kdk-lab/stacks/<name>/`** (periphery `stack_dir`) |

A Komodo **Repo** resource is **not** part of this framework — a Repo only
clones a git repo to a server to *build* something (e.g. a static-site build).
The sync + git-linked stacks need no Repo resource.

## Tags — the safety scope

Every managed resource carries `tags = ["kdk-lab"]`, and the sync is set to
`match_tags = ["kdk-lab"]`. This scopes **managed / delete-unmatched** to only
`kdk-lab`-tagged resources, so Komodo's own built-in procedures
(`Backup Core Database`, `Global Auto Update`) and anything created ad-hoc are
never pruned.

> ⚠️ `delete` (prune unmatched) is only safe **after** the `kdk-lab` tag exists
> in Komodo and the resources carry it. The tag is created the first time the
> sync applies the tagged `resources.toml`. If `match_tags` points at a tag
> that doesn't exist yet, delete falls back to "match everything" and will
> prune untagged resources. Sequence: sync with `delete` **off** → confirm the
> `kdk-lab` tag exists on the resources → **then** enable `delete`.

## The process — clone → pull → sync

1. Komodo Core clones/pulls `BigH3ater/kdk-lab-dkr@main` (poll interval 1h, or
   on demand).
2. `RunSync` reads `komodo/resources.toml` and reconciles Komodo to it
   (create/update; delete only if `delete` is enabled and the resource is
   `kdk-lab`-tagged).
3. Each git-linked stack, on deploy, re-clones its `run_directory` from the repo
   onto its host and runs `docker compose up`.

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
4. Run the sync (Komodo → **Syncs** → `kdk-lab-dkr` → Execute → Run Sync). It
   creates the stack; deploy it (or let its deploy ordering / procedure run).

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

- Source: `BigH3ater/kdk-lab-dkr` @ `main`, `resource_path = ["komodo/resources.toml"]`
- `managed = true`, `delete = false` (enable `delete` only after tag ingestion), `match_tags = ["kdk-lab"]`
- Declares: 5 servers, 24 stacks, 2 tagged procedures (`nightly-backups`, `publish-docs`). The 2 built-in procedures stay untagged/unmanaged.
