---
title: Troubleshoot Tdarr transcodes
---

# Troubleshoot Tdarr transcodes

Tdarr (3.x) runs a server + local NVENC node (`kdk-tdarr-nvenc-01`, GTX
1050 Ti) on `kdk-dkr-01`; the external Windows GPU node is no longer registered. The `tdarr-verify` one-shot pages Pushover hourly on transcode-error
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

If the nodes end up overlapping in the flow editor (the two resolution
branches drawn on top of each other, labels unreadable), re-layout the graph
with `python3 scripts/tdarr-flow-layout.py` and then push it with
`scripts/tdarr-flow-sync.sh import`. The script (stdlib only) recomputes
**only** the node `position` values — ranking nodes by longest path from the
Input node, keeping the `<=1440p` and `4K+` branches in separate vertical
lanes with the shared head/tail centered and the error/hold nodes in a
right-hand column — and asserts no node overlaps and that nothing but the
coordinates changed, so it's safe to re-run after any flow edit.

## What the flow does with the hard cases (2026-10-25)

- **Dolby Vision profile 5/7 remuxes are no longer held.** The old DV gate
  parked ~400 4K remuxes at "hold for review" since 2026-08-27, so they kept
  every foreign audio track, lossless audio and multiple default flags (the
  Super Mario Galaxy "wrong language" report). DV is always HEVC, so these files
  take the "already HEVC" remux path, which `-c copy`s the video and keeps the
  RPU/EL (verified: `ffprobe` on an ffmpeg copy of a P7 remux still shows
  `dv_profile=7 rpu_present_flag=1 el_present_flag=1`). The encoder is never
  reached for HEVC input, so the gate's concern (RPU loss on re-encode) cannot
  occur.
- **MP4 sources** (BEN.the.MEN etc.) carry `tx3g`/`mov_text` subtitles that
  Matroska cannot hold; with `forceConform=false` ffmpeg failed with
  "Subtitle codec 94213 is not supported" (exit 218). Both MKV container steps
  now `forceConform=true`, which drops those subtitle streams (Tdarr removes,
  it does not convert) and data streams.
- **AV1 sources are always re-encoded.** The 1050 Ti has no AV1 NVDEC and older
  Apple TVs cannot play AV1. A codec check sits between "already HEVC?" and the
  bitrate gate; AV1 goes to a copy of the encoder node with `hardwareDecoding=
  false` (CUDA hwaccel hard-fails on AV1 there; software decode runs ~5x
  realtime at 1080p and ~1.8x at 4K). A fresh HEVC release is still better than
  a re-encode of a lossy AV1 -- the weekly `arr-upgrade-search` (below) asks for
  one first.

## Weekly arr upgrade search (arr-upgrade-search)

Sonarr/Radarr score every on-disk file against the profile's custom formats
(AV1 = -10000), but they only replace a cutoff-unmet file when a better release
happens to appear on RSS; nothing searches backwards. The Terminal List S01
sat as AV1 from 2026-08-27 to 2026-10-25 for that reason. `stacks/
arr-upgrade-search` (Komodo procedure, Sundays 04:25) posts `EpisodeSearch` /
`MoviesSearch` for exactly the episodes/movies whose file scores below
`MIN_SCORE` (0), capped at `MAX_PER_RUN` (20) per week. The built-in
`CutoffUnmetEpisodeSearch` was rejected on purpose: it would search ~1,500
episodes (quality cutoff) every run. Mislabelled releases (title says H.265,
file is AV1) score +50 and are invisible to the *arrs; the Tdarr AV1 branch
covers those.

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

## OOM — node killed, not just the job

On 2026-10-28 07:57 the node container (`media-tdarr-node-1`) grew to ~43 GB RSS
on the 47 GB `kdk-dkr-01` host and the kernel OOM-killed it, so the whole node
went down mid-transcode instead of just the one runaway job. The usual culprits
are a **software-decoded 4K source** (AV1 branch, `hardwareDecoding=false`) or a
**DEE remux** with `-max_interleave_delta 0` (ffmpeg buffers until both streams
are ready).

The node now has `mem_limit: 24g` / `memswap_limit: 24g` (swap off for the
cgroup) and `pids_limit: 4096` in `stacks/media/compose.yaml`, so a runaway
encode hits the cgroup limit and the kernel kills *that ffmpeg* — the job fails
and the flow holds the file — while the host and the node stay up.

An OOM kill is visible as the Prometheus alert **`ContainerOOMKilled`**
(`stacks/monitoring/prometheus/rules/service.yml`, from cAdvisor
`container_oom_events_total`). If it fires for `tdarr-node` repeatedly, a single
title genuinely needs more than 24 GB (very large 4K software decode) — raise
`mem_limit` for that run, or route the title to a lower-memory path, rather than
removing the cap.

```sh
# confirm the limit is applied on the host (read-only)
ssh kdkadmin@10.1.20.20 'sudo docker inspect media-tdarr-node-1 \
  --format "mem={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}} pids={{.HostConfig.PidsLimit}}"'
```

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

## Re-queueing files from the API

The UI's *Re-queue* is a document update: `cruddb getById` on `FileJSONDB`,
set `TranscodeDecisionMaker` to `Queued` (and `holdUntil` to 0), `cruddb update`
with the whole document. `stacks/tdarr-backlog/backlog.sh` does exactly this.
Files held for review never show as "Transcode error" -- find them from the
job reports (`grep -l "verdict: requireReview" DB2/JobReports/*/*transcode*`).

## Backlog feeder (tdarr-backlog)

After the 2026-10-25 flow changes ~345 library files (12.7 TB) needed another
pass: Dolby Vision remuxes the old gate had held (lossless audio, foreign
tracks), MP4 sources, AV1 encodes, files with foreign audio/subs or cover
art. Queuing them all at once would park new downloads behind days of
remuxes, so `/etc/kdk/tdarr-backlog.list` on `kdk-dkr-01` (highest value
first) is drained by the hourly `tdarr-backlog` procedure: when fewer than
`MIN_QUEUED` (3) files are Queued it re-queues the next `BATCH` (6). Empty list
= no-op. While it drains, the node runs **one** transcode worker so two
lossless jobs cannot collide on the single-slot DEE wrapper (the DEE step
retries a 503 for 5 minutes, then falls back to native E-AC3 and the Atmos
bed is lost). The first run that finds the list empty raises the node back to
`TARGET_GPU_WORKERS` (2) through `alter-worker-limit` and sends one Pushover
note ("tdarr-backlog done"); after that every run is a no-op.

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
