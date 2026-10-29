---
title: Komodo GitOps (Resource Sync)
tags: [homelab/process, komodo, gitops]
updated: 2026-10-28
---

# Komodo GitOps — Resource Sync

How this repo drives Komodo. Every Docker stack on every host is declared in
`komodo/resources.toml` and cloned/deployed from this repo by Komodo. There is
no hand-managed Docker outside Komodo.

**Related:** [[backlog]] · `komodo/resources.toml`

## Access

The UI at `komodo.kmkdp.com` is behind Authelia SSO and Komodo self-registration is
closed (`stacks/komodo/compose.yaml`). The loop below does not depend on SSO: the
actions call Core at `http://localhost:9120`, and API-key calls (`X-Api-Key` on
`/read`, `/write`, `/execute`) plus the `/listener` webhooks have their own Traefik
routers without ForwardAuth. Details: [[komodo]].

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

1. Start from the template: `cp -r stacks/_template stacks/<name>` (the
   `x-hardened` anchor, an `internal: true` db network, Traefik labels, and
   secrets via `.env.tpl` + `op inject` in a `pre_deploy` command). Then run
   `python3 scripts/compose-baseline-check.py --stack <name>`. It must come back
   clean, or every gap needs a documented skip/exception label; see
   [Compose hardening baseline](reference/compose-baseline.md). The
   `compose-baseline` GitHub Action runs the same check on the PR.
2. Declare it in `komodo/resources.toml`:
   ```toml
   [[stack]]
   tags = ["kdk-lab"]
   name = "<name>"
   [stack.config]
   server = "<server>"          # kdk-dkr-01 | kdk-dkr-dmz-01 | kdk-dkr-02 | kdk-dkr-03 | kdk-mon-01 | kdk-log-01
   repo = "BigH3ater/kdk-lab-dkr"
   run_directory = "stacks/<name>"
   webhook_enabled = true
   ```
3. Commit + push to `main`.
4. Done — within 15 min `gitops-sync` applies it (creates the stack tagged
   `kdk-lab`; `deploy = true` stacks deploy in `after`-order). To apply
   immediately, Execute the `gitops-sync` Procedure in the UI instead.

## Dependency updates (Renovate)

Image versions are pinned to full tags in every `compose.yaml` and
`Dockerfile`; nothing floats (`:latest`, `:stable`, `:3.20`) except four
documented cases: `cturra/ntp:latest` (the image publishes no other tag;
Renovate pins its digest), the Immich database and cache images
(`ghcr.io/immich-app/postgres:14-vectorchord…`, `valkey:9`, both digest-pinned
and copied from Immich's own release compose, tag bumps disabled), and the
locally built `kdk/*:latest` images. The Mend
Renovate GitHub App proposes the bumps; config is `renovate.json` at the repo
root. Renovate never merges anything (`automerge: false`).

**How PRs arrive**

- On **weekends** (America/Chicago), at most 5 open PRs and 2 new per hour.
  Vulnerability fixes (GitHub/OSV advisories) are raised at any time and
  labelled `security`. Those advisories cover language packages (pypi, npm,
  …), not container images, so today they rarely fire here. Image CVE fixes
  arrive through the normal patch PRs.
- **`renovate/dmz-patch`**: one PR with every patch + digest update for stacks
  on kdk-dkr-dmz-01 (`dmz-proxy`, `identity`, `jellyfin`, `wizarr`, `dmz-apps`,
  `immich`, `tandoor`, `audiobookshelf`).
- **`renovate/internal-patch`**: the same for every other stack.
- **Minor** bumps: one PR per image (e.g. `renovate/python-3.x` covers every
  `python:` reference, compose and Dockerfile alike).
- **Major** bumps (e.g. postgres 16 → 18, node-red 4 → 5) get no PR until you
  tick the box next to them on the **Dependency Dashboard** issue. Two
  exceptions need no tick: the `postgres` image in `backup-*` stacks (it is only
  the `pg_dump` client; a newer client dumps an older server) and GitHub
  Actions majors (CI only). `python` base images are capped at `<3.14` until a
  test build proves the pinned packages have wheels for the newer release.
- Auth/edge images (authelia, lldap, traefik, cloudflared) also get the
  `identity` label.
- `docker:pinDigests` is on: the first run (and every digest refresh after it)
  writes `tag@sha256:…` into the image lines. That first wave lands inside the
  two patch PRs and **recreates every container they touch**.

**What a merge does.** A merge to `main` deploys within 15 minutes through
`gitops-sync`. **Any change to an image string recreates that container**,
even when the new tag resolves to the same image. Treat each merge as a planned
restart of the services in its diff, and merge on a quiet evening if the PR
touches `home-assistant`, `scrypted-homebridge`, `proxy`, `dmz-proxy`,
`identity`, `komodo`, `tailscale-subnet-router` or `tailscale-dns`.

**Merge order** (one at a time, wait for the deploy before the next):

1. Anything labelled `security`.
2. PRs touching `dmz-proxy` (public edge): `renovate/dmz-patch` first (it
   also carries `identity`), then a traefik/cloudflared minor → check
   `https://<any public host>` loads.
3. PRs touching `identity` (Authelia / LLDAP / Redis / Postgres) → check an
   Authelia login.
4. Everything else: `renovate/internal-patch`, then the minors.

**How `deploy-changed` decides (since 2026-10-29).** It reads HEAD from the
GitHub API and, per stack, the list of files changed between the stack's
deployed commit and HEAD (compare API). A stack is deployed only when a file
under its `run_directory` changed; a `komodo/resources.toml` change deploys
every stack not at HEAD (the patch does not say which stack it belongs to).
Komodo's own `latest_hash` is only the fallback when GitHub is unreachable: it
is a cache that lags HEAD after a burst of merges. The action lists stacks per
server because `ListStacks` returns at most 50.

**Not covered by an automatic deploy:**

- `komodo` is `deploy = false` **and** excluded from the `deploy-changed` action
  (since 2026-10-28): deploying it from inside Core restarts Core mid-procedure
  ("Komodo shutdown during execution") and leaves every later stack for the next
  tick. The action logs `komodo stack behind … apply by hand` instead. Apply it on
  dkr-01 from Komodo's own clone: `cd /opt/kdk-lab/stacks/komodo/stacks/komodo &&
  sudo docker compose up -d` (the pre_deploy `op inject` has already rendered
  `.env` there), or Deploy the stack from the Komodo UI while nothing else runs.
- One-shot stacks (`backup-*`, `tdarr-*`, `arr-*`, `storage-health`, …) pick
  up the new tag on their next scheduled run.
- Local builds (`kdk/*:latest`, `run_build = true`) are ignored by Renovate;
  their `FROM python:…` line is updated, and the image rebuilds on the stack's
  next deploy.
- A PR that only changes a file mounted through compose `configs:` (or a
  single-file bind mount) does not change the compose spec, so the deploy does
  not restart anything. Run **`RestartStack`** for that stack in Komodo.

**NUT is excluded.** `stacks/nut/` never gets a version PR; a digest refresh
of `instantlinux/nut-upsd` waits for a Dependency Dashboard tick. A NUT
recreate once shut down kdk-hyp-01: only approve it with a person watching.

kdk-mon-01 has `auto_pull = false` (Docker Hub rate-limits the Pi). A changed
tag still pulls on `compose up` because the image is missing; if the pull is
denied, pull it on the Pi by hand and redeploy.

## Image cleanup — weekly prune

Docker never deletes old images by itself. The `prune-images` Procedure
(Sunday 05:10 America/Chicago) runs Komodo's `PruneImages` execution
(`docker image prune -a -f`) on every `[[server]]`: kdk-dkr-01, kdk-dkr-dmz-01,
kdk-mon-01, kdk-dkr-02, kdk-dkr-03. kdk-log-01 has had a `[[server]]` since 2026-10-28 (PR #67) but is not in the stage yet: add it.

- Removes every image that **no container references** — running or exited.
- A one-shot's image (backup workers, `arr-*`, `planner-service`, …) **stays**:
  its exited container still references it. A stack that is fully `down`
  loses its image and re-pulls (or rebuilds) it on the next deploy.
- A Renovate bump leaves the **old tag on disk until Sunday**; a full root disk
  right after a pin wave is expected to clear then. To reclaim now, Execute
  `prune-images` (Komodo → Procedures) or Server → Docker → Images → Prune.
- Never `PruneSystem`: it adds `--volumes` and would delete named volumes of
  stopped stacks.

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
- Declares: 5 servers, 1 repo, 27 stacks, 3 tagged procedures (`nightly-backups`,
  `publish-docs`, `gitops-sync`), 1 action (`gitops-reconcile`). The 2 built-in procedures stay
  untagged/unmanaged.
