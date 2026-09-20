---
title: Troubleshoot Tdarr transcodes
---

# Troubleshoot Tdarr transcodes

Tdarr runs a server + local NVENC node on `kdk-dkr-01` and an external Windows
GPU node. The `tdarr-verify` one-shot pages Pushover hourly on transcode-error
rises, health-check errors, and `.iso` files it can't process. A second one-shot,
`tdarr-remediate`, self-heals **corrupt** files by driving Radarr/Sonarr — see
[Self-healing corrupt files](#self-healing-corrupt-files-tdarr-remediate).

## The flow is tracked in git

The transcode flow (`hevc-pipeline`, the only flow; both libraries point at
it) is version-controlled at `stacks/media/tdarr-flows/hevc-pipeline.json`.
Sync it with `scripts/tdarr-flow-sync.sh export|diff|import` (repo is the
source of truth; `import` pushes via the cruddb API and restarts tdarr so the
server drops its in-memory copy — verified the API persists flow updates).
After editing the flow in the tdarr UI instead, run `export` and commit so
the repo copy doesn't go stale.

## Lossless-audio titles error → check DEE first

The most common failure: a batch of **transcode errors, all on lossless audio**
(DTS-HD MA / TrueHD). Those route through the DEE wrapper (lossless → Dolby
DD+); if it's down they all fail with a terminal "Failed to move file" after the
native-E-AC3 fallback.

```sh
curl -s http://10.1.1.130:8484/healthz
```

If that's not `status: ok`, fix DEE first — see
[Troubleshoot the Windows DEE VM](windows-vm-dee.md) — then re-queue the errored
files. The `tdarr-verify` alert text already correlates the two: it says whether
the transcode-error rise coincides with DEE being down.

## Single-worker concurrency (Atmos preservation)

The DEE wrapper is **single-slot**. With more than one transcode worker, a
second dispatch gets `503 encoder busy` and the flow falls back to native
ffmpeg E-AC-3 — which **loses Atmos** on Atmos titles. Keep the DEE-dispatching
node at **one** transcode worker (`transcodegpu:1`, `transcodecpu:0`) so
dispatches serialize. `autoAcceptTranscodes` is off, so outputs wait for manual
accept and originals stay intact.

## Node flapping / jobs stuck in limbo

The external GPU node deregisters/reregisters intermittently; when it drops,
its in-flight files leave the staging queue after ~300s and sit in limbo.
Symptoms: jobs "assigned" to a node that isn't connected, or a live node with 0
workers. Check connect/deregister events in the server log; set worker counts
with `POST /api/v2/alter-worker-limit {nodeID, process, workerType}`.

`get-nodes` is **gone** in Tdarr 2.86 (404). Node *configs* are in `NodeJSONDB`
via `cruddb`, but that's config, not liveness — use the server log for who's
actually connected.

## Health-check errors and .iso files

`HealthCheck: Error` is usually FFprobe returning `{}` on an unreadable or
corrupt file (some WEB-DLs, a bad remux). `.iso` files can't be transcoded at
all (BR-DISK rips) — they need remuxing to MKV upstream. `tdarr-verify` lists
both when their counts rise; they're data hygiene, not a pipeline fault.

## Re-queueing errored files

`cruddb` mode `update` returns `200` but does **not** change
`TranscodeDecisionMaker`, so errored files stay "held for review." Re-queue them
from the **Tdarr UI**: select the errored files → Re-queue. Counts are read from
`FileJSONDB` (Tdarr 2.86 stores state in SQLite at
`/app/server/Tdarr/DB2/SQL/database.db`; there's no `sqlite3` in the container —
go through the `cruddb` API).

## Self-healing corrupt files (tdarr-remediate)

`stacks/tdarr-remediate` (python:3.12-alpine, `network_mode: host` on
`kdk-dkr-01`) runs hourly via a Komodo procedure (`0 50 * * * *`, after
`tdarr-verify` at `:40` and clear of the `gitops-sync` slots). For every file
Tdarr marks `HealthCheck: Error`, it finds the title in Radarr (movies) or
Sonarr (TV) and **blocklists the grabbed release, deletes the file, and triggers
a fresh search** — so a corrupt grab is replaced automatically instead of
sitting broken.

It routes by path prefix (`/data/media/movies` → Radarr, `/data/media/tv` →
Sonarr) and reads each app's API key straight from the mounted (read-only)
`config.xml`, so there are **no new secrets**. State (per-title attempt counts,
dedup, and paths pending Tdarr rescan) persists in
`/etc/kdk/tdarr-remediate.state`.

### Why it ignores transcode errors

It acts **only** on `HealthCheck: Error` (a genuinely unreadable/corrupt file).
It never touches `TranscodeDecisionMaker: Transcode error`, because that is
usually an **infra** fault — e.g. the DEE wrapper outage that failed ~78 *good*
files. Blocklisting and redownloading those would churn perfectly good releases
across the whole library. Transcode errors stay the job of `tdarr-verify`.

### Guardrails

- **`DRY_RUN`** (compose env) — `"1"` (default when first shipped) reports over
  Pushover exactly what it *would* do and mutates nothing; `"0"` acts for real.
- **`MAX_BATCH`** (default `5`) — a circuit breaker. If more files fail at once
  than bad releases plausibly explain, that's systemic (ffmpeg/storage/mount);
  it pages for manual review instead of mass-deleting.
- **`MAX_ATTEMPTS`** (default `3`) — per-title cap. If the same movie/episode
  comes back corrupt after N blocklist+redownload cycles, every available
  release may be bad; it gives up and pages rather than looping. Keyed on the
  stable Radarr `movieId` / Sonarr `episodeId` so the count survives redownloads.

It is silent when there's nothing new: already-reported failures aren't re-paged,
and paths it deleted are suppressed until Tdarr's library scan drops the stale
record.

### Enable it / flip back to dry-run

Edit `DRY_RUN` in `stacks/tdarr-remediate/compose.yaml`, commit, and redeploy
the stack (Komodo `DeployStack tdarr-remediate`, or wait for the next `:50`
run). Run it on demand the same way.

### Clearing the per-title cap

If a title hit `MAX_ATTEMPTS` and you've fixed the source (better release
available, indexer issue resolved), drop its entry from
`/etc/kdk/tdarr-remediate.state` on `kdk-dkr-01` (the `attempts` map) so it's
eligible again on the next run.

## The verify one-shot

`stacks/tdarr-verify` (alpine, `network_mode: host` on `kdk-dkr-01`) runs hourly
via a Komodo procedure (`0 40 * * * *`, off the `gitops-sync` slots). It pages
on: DEE `/healthz` ≠ 200/ok (priority 1), the Tdarr server unreachable
(priority 1), and a rise in transcode-error / health-error / iso counts against
the baselines in `/etc/kdk/tdarr-verify.count`. It's silent when clean. To
extend it, see [Add a Pushover alert](pushover-alerts.md).
