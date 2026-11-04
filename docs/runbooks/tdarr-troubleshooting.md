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
`diff` compares the **parsed** JSON (sorted keys, non-ASCII literal), so the
export's `\u00a7` vs the repo's `§` is no longer reported as a mismatch.
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
curl -s http://10.1.1.130:8484/healthz   # kdk-dee-01
curl -s http://10.1.1.131:8484/healthz   # kdk-dee-02
```

The flow talks to neither directly: it posts to **dee-lb** (HAProxy on
`kdk-dkr-01`, `10.1.20.20:8484`, stats on `:8405`), which fronts both wrappers.
If a wrapper is not `status: ok`, fix DEE first — see
[Troubleshoot the Windows DEE VM](windows-vm-dee.md) — then re-queue the errored
files. The `tdarr-verify` alert text already correlates the two: it says whether
the transcode-error rise coincides with DEE being down.

## DEE remux OOM: cover art + sparse subtitles (fixed 2026-11-02)

The §7.4b DEE step remuxes the new DD+ track back with `-max_interleave_delta 0`
(needed: without it Neptune/Apple TV direct play is silent). That option makes
ffmpeg hold video in RAM until **every** mapped stream has a packet. Two kinds of
stream never deliver one in time:

- an MKV **cover-art picture** (mjpeg, one packet) — `-map 0:v` includes it;
  `-map 0:V` (capital) excludes attached pictures;
- a **sparse forced-only PGS** subtitle track, which can go an hour between packets.

On 4K DV remuxes this grew past the 36 GiB node cap (`ContainerOOMKilled`), the
job fell back to the native E-AC3 encode and the title **lost its Atmos**.
The step now remuxes in **two passes**: (1) `-map 0:V` + the new audio with
`-max_interleave_delta 0`; (2) add subtitles and chapters from the source with
the default interleave. Measured on Minority Report (88 GB, cover art + two
English PGS): old one-pass killed at 11.7 GB in ~35 s; two-pass peak **40 MB**
(pass 1) and 208 MB (pass 2); audio within 2–5 MB of its video at 0/10/30/60/90 min.

### When the DEE step fails anyway (2026-11-04)

- **Pass 2 retry without subtitles.** If pass 2 (subs + chapters) fails for any
  reason other than an OOM kill (ffmpeg `SIGKILL`), the step retries **once**
  from the same pass-1 file with video + the new DD+ audio + chapters and **no
  subtitles**. Atmos is kept; only the subs are lost. Pass 1 and its
  `-max_interleave_delta 0` are unchanged. A "subs dropped" output is findable
  two ways:
  - the job report has a `[dee] SUBS DROPPED:` line;
  - the file carries the global tag `KDK_DEE_SUBS_DROPPED=1`:
    `ffprobe -v error -show_entries format_tags=KDK_DEE_SUBS_DROPPED -of csv=p=0 <file>`
    (prints `1`). Re-grab or re-run those titles when you have time.
- **Native E-AC3 fallback** (pass 1 failed, the retry failed, an OOM, or the
  wrapper failed) keeps subs but **loses Atmos**. On Atmos sources the job report
  says `ATMOS LOST`. This is how 42 Atmos-tagged library titles ended up as plain
  6-channel E-AC3 by 2026-11-04 (list in the vault, Tdarr review 2026-11-04 §8).
- **The cause is logged.** On every failure the job report has the **last
  2000 characters** of ffmpeg's stderr (it used to keep the first 300 characters
  of the error, which were just the command line, so the 2026-11-03 Two Towers
  pass-2 cause was lost). ffmpeg runs with `-nostats` and a 64 MiB pipe buffer:
  Node's `execFileSync` default of 1 MiB would SIGTERM ffmpeg (`ENOBUFS`) if a
  long remux printed more than that.
- **No orphans on the exchange share.** The `catch` path now also removes the
  job's `outbound/*.tdarr.ec3`, not just `inbound/`. Before this, every "DEE ok,
  remux failed" job left its encoded track behind (27 files, 28 GB, by
  2026-11-04). After a forced failure,
  `ssh kdkadmin@10.1.20.20 'ls -l /mnt/dee-exchange/outbound/*.ec3'` should show
  nothing new.

## OOM — node killed, not just the job

On 2026-10-28 07:57 the node container (`media-tdarr-node-1`) grew to ~43 GB RSS
on the 47 GB `kdk-dkr-01` host and the kernel OOM-killed it, so the whole node
went down mid-transcode instead of just the one runaway job. The usual culprits
are a **software-decoded 4K source** (AV1 branch, `hardwareDecoding=false`) or a
**DEE remux** with `-max_interleave_delta 0` (ffmpeg buffers until both streams
are ready).

The node has `mem_limit: 36g` / `memswap_limit: 36g` (swap off for the
cgroup) and `pids_limit: 4096` in `stacks/media/compose.yaml` (raised from 24g
on 2026-10-31; a PR to lower it to **16g** is pending, because since the
two-pass remux node RSS has peaked at 2.1 GiB). A runaway
encode hits the cgroup limit and the kernel kills *that ffmpeg* — the job fails
and the flow holds the file — while the host and the node stay up.

An OOM kill is visible as the Prometheus alert **`ContainerOOMKilled`**
(`stacks/monitoring/prometheus/rules/service.yml`, from cAdvisor
`container_oom_events_total`). If it fires for `tdarr-node` repeatedly, a single
title genuinely needs more than the cap (very large 4K software decode) — raise
`mem_limit` for that run, or route the title to a lower-memory path, rather than
removing the cap.

```sh
# confirm the limit is applied on the host (read-only)
ssh kdkadmin@10.1.20.20 'sudo docker inspect media-tdarr-node-1 \
  --format "mem={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}} pids={{.HostConfig.PidsLimit}}"'
```

## DEE concurrency: two slots behind dee-lb (Atmos preservation)

Each DEE wrapper is **single-slot**, but there are two of them (`kdk-dee-01`,
`kdk-dee-02`) behind **dee-lb** (HAProxy, `leastconn`, `maxconn 1` per
backend). The node runs **two** transcode workers (`transcodegpu:2`,
`transcodecpu:0`; `TARGET_GPU_WORKERS=2` in `tdarr-backlog`). When both slots
are busy the LB queues the request. If the flow still gets a `503`, it **waits**
(60 × 60 s, up to 1 h) instead of falling back to native E-AC3, which would lose
Atmos. Drop to `transcodegpu:1` only if one wrapper is down for a long time.

**Outputs replace originals automatically.** No `autoAcceptTranscodes` gate
applies here: the libraries run the flow, and step "8. PASS" is
`replaceOriginalFile`. A file is replaced in place once step 7 passes (video end
time within 99.9–100.1 % and, since 2026-11-03, not all audio lost). Files that
fail step 7 go to `/data/quarantine` or are held for review.

## Foreign-audio rule (2026-11-03)

Step "6. Real (non-commentary) English audio present?" (`langGate1080` /
`langGate4k`, custom functions) drops foreign audio (6a) **only if** there is
an English track whose title does not say commentary or description. If not
(e.g. *Ran*: Japanese main track plus English commentaries only), 6a is skipped
and the foreign main track is kept. 6b still drops commentary. Step 7 also
refuses to replace a file that had audio with one that has none. This happened
to *Ran* and *Incendies*, which were re-grabbed.

## Long DEE encodes: the 7200 s timeout

The wrapper kills a DeeZy encode after `DEE_WRAPPER_TIMEOUT_SECONDS` (default
**7200 s**, `tools/dee-wrapper/main.py`). The flow's HTTP socket timeout is
**7800 s**, so it always outlasts the wrapper. Long 4K TrueHD Atmos titles can
exceed 2 h. *Return of the King* Extended timed out on 2026-11-03 and fell back
to plain E-AC3. To fix that, raise `DEE_WRAPPER_TIMEOUT_SECONDS` on **both**
Windows wrappers (see [Troubleshoot the Windows DEE VM](windows-vm-dee.md)) and
raise the flow's socket timeout in `dee1` so it stays above the wrapper's value.

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
= no-op. (Until 2026-10-29 the node ran **one** worker while the list
drained, because there was only one DEE slot. With two wrappers behind dee-lb
and the 1 h busy wait it runs two.) The first run that finds the list empty
raises the node to `TARGET_GPU_WORKERS` (2) through `alter-worker-limit` and sends one Pushover
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
via a Komodo procedure (`0 40 * * * *`, off the `gitops-sync` slots). The alert
logic lives in `verify.sh`, which sources `lib.sh` (DEE health classification +
alert de-duplication); both are bind-mounted into the container. It is silent
when clean. To extend it, see [Add a Pushover alert](pushover-alerts.md).

### Alert semantics (de-duplicated, 2026-11-03)

Every alert is a **signal** with a stable **fingerprint**, a severity
(`fail`/`warn`) and a kind (`sticky`/`edge`). The fingerprint keys on the
problem's identity and **never on changing numbers** (e.g. free-GB), so the same
ongoing problem fingerprints identically every run.

- **DEE `/healthz`** is probed on **both** wrappers (`kdk-dee-01` 10.1.1.130 and
  `kdk-dee-02` 10.1.1.131). With **one** wrapper sick and the other healthy
  (`dee_up >= 1`), that is a **WARN** (priority 0) — Tdarr keeps working at half
  DEE capacity. **Both** down/degraded is a **FAIL** (priority 1). DEE
  fingerprints are `dee:<host>:<status>:<reason>`, e.g.
  `dee:kdk-dee-01:degraded:low-disk`. The message carries the `*_free_gb` vs
  `min_free_gb` values and says **"low disk on C: (DeeZy temp?)"** when the work
  drive is at/under the floor — that is the DeeZy temp-folder pile-up (see
  [Troubleshoot the Windows DEE VM](windows-vm-dee.md); the wrapper now sweeps
  them, `tools/dee-wrapper`).
- **Tdarr server unreachable** is a `sticky` FAIL (`tdarr:server:unreachable`).
- **Transcode-error / health-error / iso rises, interleave fix results, and
  quarantine moves** are `edge` signals (transcode rise = FAIL; the rest WARN),
  fingerprinted per class or per file (e.g. `interleave-fail:<name>`).

**De-dup rule:** a signal is notified only when its fingerprint is **new**, has
**changed**, or has been continuously present for **24 h** (one daily reminder).
So an unchanged problem never repeats hourly — the regression this replaced was
`kdk-dee-01` paging a priority-1 FAIL every hour for 8 hours on 2026-11-03 while
`kdk-dee-02` stayed healthy. When a previously alerted **sticky** fingerprint
clears, one priority-0 **"recovered"** message is sent; `edge` events clear
silently. Count baselines persist in `/etc/kdk/tdarr-verify.count`; alert state
(fingerprints + first-seen/last-notified) in `/etc/kdk/tdarr-verify.alerts`.

Offline tests for the classification and de-dup logic:
`sh stacks/tdarr-verify/tests/run-tests.sh` (stubs `curl`/`push`, injects the
state file and clock; no network or VM).
