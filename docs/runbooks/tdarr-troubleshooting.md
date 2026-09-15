---
title: Troubleshoot Tdarr transcodes
---

# Troubleshoot Tdarr transcodes

Tdarr runs a server + local NVENC node on `kdk-dkr-01` and an external Windows
GPU node. The `tdarr-verify` one-shot pages Pushover hourly on transcode-error
rises, health-check errors, and `.iso` files it can't process.

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

## The verify one-shot

`stacks/tdarr-verify` (alpine, `network_mode: host` on `kdk-dkr-01`) runs hourly
via a Komodo procedure (`0 40 * * * *`, off the `gitops-sync` slots). It pages
on: DEE `/healthz` ≠ 200/ok (priority 1), the Tdarr server unreachable
(priority 1), and a rise in transcode-error / health-error / iso counts against
the baselines in `/etc/kdk/tdarr-verify.count`. It's silent when clean. To
extend it, see [Add a Pushover alert](pushover-alerts.md).
