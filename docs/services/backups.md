---
title: Backups
host: kdk-dkr-01, kdk-dkr-dmz-01
stack: backup-dkr-01, backup-dmz
tier: infra
status: live
storage: yes
secrets: [proton-mirror-crypt, authelia-postgres, immich-postgres, jellystat, wger-postgres, authelia-app-secrets]
updated: 2026-11-16
tags: [homelab/stack, tier/infra, status/live]
---

# Backups

Nightly app-state backups run as one-shot stacks that a Komodo Procedure redeploys on a schedule; local NAS is armed, and encrypted offsite to Proton runs every night. The offsite stage now **packs one `tar.zst` per host** before uploading, so rclone ships a handful of large objects instead of thousands of small files the Proton API could not keep up with.

**Related:** [[kdk-dkr-01]] · [[kdk-dkr-dmz-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | pg dumps + `/opt/kdk-lab` rsync to the NAS, then selected sets offsite |
| Status | 🟢 Local NAS live (nightly 03:20) · 🟢 offsite packs per-host `tar.zst` (+ the CAD library `3d-prints-*.tar.zst`) then syncs `offsite/` + `pve-templates/` + `*/pgdump/` to Proton |
| Hosts | [[kdk-dkr-01]] (`backup-dkr-01`), [[kdk-dkr-dmz-01]] (`backup-dmz`) |
| Deploy | Komodo Procedure `nightly-backups` redeploys both stacks (`deploy = false` otherwise) |
| Destinations | `/mnt/backup/dkr/<host>` (NFS tankz3) → Proton Drive (crypt) |
| Blast radius | No new restore points; existing backups intact |

## Verify

```bash
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-01 | head'                    # recent state
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-01/pgdump | head'             # jellystat + wger .sql.gz
ssh kdkadmin@10.1.20.20 'ls -t /mnt/backup/dkr/kdk-dkr-dmz-01/pgdump | head'         # dated .sql.gz
```
```bash
ssh kdkadmin@10.1.20.22 'sudo ls -la /mnt/backup/dkr/offsite/ | grep 3d-prints'   # CAD library archive, newest 3
```
In Komodo, Procedure `nightly-backups` shows a recent successful run.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| No new dumps | Procedure didn't run / stack failed | Komodo Procedure history; `docker logs <backup-container>` |
| Offsite pass exits `rc=10` | the pass hit `--max-duration 25m` before the (now much smaller) delta synced | the next pass resumes; with per-host archives a clean night should reach `rc=0` in one or two passes. `rc=0` in `/mnt/backup/dkr/.offsite-status` means fully in sync |
| Offsite pack wrote nothing / `tar` failed | `apk add tar zstd` could not reach the Alpine CDN, or a host dir was absent | `docker logs` the `backup-offsite-pack-*` container; `sudo ls -la /mnt/backup/dkr/offsite/` shows today's `<host>-*.tar.zst` |
| `3d-prints archive missing/empty` | `/mnt/3d-prints` not mounted on kdk-dkr-04 (pack skips an empty source) | `ssh kdkadmin@10.1.20.22 findmnt /mnt/3d-prints`; [[cad-library]] step 2 |
| WARN `laptop cad-sync last ran Nd ago` | the laptop has been away/asleep, or its timer is off | on the laptop: `systemctl --user list-timers cad-sync.timer`, `journalctl --user -u cad-sync -n 20` ([[cad-library]]) |
| Offsite accumulates dead objects | crypt password changed without a purge | `rclone purge proton:kdk-lab-backup` before the first sync under a new password |
| rclone can't read cached tokens | image newer than the login version | pin `rclone/rclone:1.75.2` (the version that did the interactive login) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | `/mnt/backup` (NFS tankz3) | nowhere to write |
| Needs | [[identity]] + [[immich]] + Tandoor Postgres | pg dumps |
| Needs | Proton Drive (offsite only) | offsite tier |
| Needs | `/mnt/3d-prints` on kdk-dkr-04 (ro NFS of `tankz3/3d-prints`) | CAD library archive; `backup-verify` fails the night |

## Observability

Grafana → **Backups → Nightly backups** (`stacks/monitoring/grafana/dashboards/Backups/nightly.json`, uid `backups-nightly`, default range 7 d, Loki datasource `loki-dkr01`). It replaced the stale Containers → Backups dashboard (uid `kdk-backups`, deleted). Komodo's Procedure history and the `backup-verify` Pushover stay the source of truth; the dashboard shows what the night produced.

| Row | Shows | Source |
|---|---|---|
| Tonight at a glance | backup-verify result, steps failed, bytes produced, upload passes + time, On Proton (last `offsite rclone exit=`), latest exit per step, steps per night | docker-event-watch `event name=<container> action=die code=N` (every host, last 26 h) + the offsite lines |
| Files produced tonight | name, host, kind, series, size, prev size, change, % of prev (red < 50), Proton status | `backup-manifest` lines (last 23 h) |
| Sizes over time (30 d) | per-host archives, pg dumps per database (log scale) | `backup-manifest` |
| CAD library changes | added/changed/removed per night, the changed paths | `backup-change` / `backup-change-summary` |
| Upload passes | exit per pass per night (10 = 25-min cap), passes per night, upload time | `offsite pass N: rclone exit=N secs=S` |

The lines `pack.sh` writes after packing (stdout → Alloy on kdk-dkr-04 → Loki `{stack="backup-offsite", service="pack"}`), all logfmt:

```text
backup-manifest date=2026-11-16 kind=archive host=kdk-dkr-01 series=kdk-dkr-01 name=kdk-dkr-01-2026-11-16.tar.zst bytes=4902057153 prev_bytes=4882992101 pct=100.4
backup-manifest date=2026-11-16 kind=pgdump host=kdk-dkr-dmz-01 series=immich name=immich-2026-11-16.sql.gz bytes=18299692 prev_bytes=18299692 pct=100.0
backup-change date=2026-11-16 op=changed path="relay-bracket/relay bracket.sc"
backup-change-summary date=2026-11-16 added=1 changed=1 removed=0 truncated=false baseline=false files=157
```

- One `backup-manifest` per archive in `offsite/` and per `<host>/pgdump/` dump dated tonight (UTC date). `prev_bytes` = the same series' previous file (yesterday's archive, the previous dump of that DB), `none` for a first file; `pct` = bytes × 100 / prev_bytes, computed in pack because LogQL cannot compare two labels. `series` = the host for archives (`3d-prints` for the CAD library), the database for dumps.
- `backup-change`: the newest `3d-prints` archive's file list (path, size, mtime) against the previous archive's, at most 300 lines, paths always quoted (`\` and `"` escaped; GNU tar's own escapes for odd characters stay). The summary carries the real counts and `truncated=true` past 300. With no previous archive it writes one summary line with `baseline=true`.
- Reporting is best-effort (a subshell): it never fails or delays pack.
- `offsite.sh` adds `secs=` to each `offsite pass N: rclone exit=N` line and `passes=N secs=S` to the final `offsite rclone exit=N` line (the `rclone exit=N` text is unchanged).
- Alert `BackupFileShrank` (`stacks/loki/rules/backups.yml`, warning → Pushover like every Loki warning): a manifest line with `pct < 50`, labels `host` + `name`, fires for 2 h after the run. Check the Files table, then the previous file on kdk-dkr-04 (`sudo ls -l /mnt/backup/dkr/offsite/` or `/mnt/backup/dkr/<host>/pgdump/`) before it rotates out (archives keep 3, dumps keep 15).

**Re-emit the report without a backup run** (`pack.sh --report-only [YYYY-MM-DD]`): reads the files already on disk, writes the same lines, and does not pack, rotate, copy the CAD marker or write anything under `/mnt/backup`. The date defaults to the newest archive date in `offsite/`. It then holds the container `REPORT_HOLD` seconds (90): Alloy's `discovery.docker` refreshes every 60 s and only sees running containers, so a container that exits sooner never reaches Loki. Run it from a repo checkout on kdk-dkr-04, **not** the Komodo clone, outside the nightly window (03:20 until the offsite step ends):

```bash
ssh kdkadmin@10.1.20.22
git clone --depth 1 -b main https://github.com/BigH3ater/kdk-lab-dkr.git /tmp/kdk-report && cd /tmp/kdk-report/stacks/backup-offsite
sudo docker compose run --rm --no-deps --entrypoint /bin/sh pack /pack.sh --report-only    # ~95 s; prints the lines
cd / && rm -rf /tmp/kdk-report
```

`name: backup-offsite` in `compose.yaml` makes the run container (`backup-offsite-pack-run-<id>`) carry the same Loki labels as the nightly one (`stack="backup-offsite"`, `service="pack"`, `host="kdk-dkr-04"`): Alloy takes `stack` and `service` from the `com.docker.compose.project` / `.service` container labels (`stacks/observability-agent/alloy/config.alloy`). The dashboard's step panels ignore `*-run-*` containers.

**Known gap:** the dkr-01 one-shots' own output (pgdump, rsync, collect, verify) never reaches Loki: they exit before Alloy's next 60 s discovery. Their exit codes do arrive (docker-event-watch), so the step panels are complete; only their log lines are missing. Tracked in the vault `10.Homelab/TODO.README.md`.

**Proton leftovers, decision pending.** Besides `offsite/`, `pve-templates/` and `*/pgdump/`, the Proton crypt root still holds the per-host folders of the old whole-tree sync, frozen since before 2026-10-31 (~10.4k files), and 47 `_archive/` days. rclone no longer touches them (outside the `--filter` set). The owner has not decided whether to remove them: **do not delete them, and do not script a delete.** They are visible read-only through the laptop mount ([CAD library → Validate offsite from the laptop](../runbooks/cad-library.md#validate-offsite-from-the-laptop)).

## Architecture

Three layers: (1) ZFS snapshots — vmpool covers the DMZ VM zvol, tankz3 covers bulk + backup datasets; (2) VM-level — vzdump of VM 300 and VM 320 (kdk-log-01: Loki data lives on its disk, no app-level backup) to PBS, TrueNAS zvol replication of the DMZ VM; (3) app-level — the two backup stacks. `backup-dmz` runs `pg_dump` for Authelia, Immich and Tandoor (keeps 15) then rsyncs state; `backup-dkr-01` runs `pg_dump` for Jellystat and wger (keeps 15 each, into `kdk-dkr-01/pgdump`; wger without PowerSync's derived `powersync` schema) then rsyncs `/opt/kdk-lab` (excluding scratch, the live Postgres cluster dirs `komodo/postgres`, `jellystat/postgres` and `wger/postgres`, and the regenerable `wger/static`), then the separate `backup-offsite` stage first **packs one `tar.zst` per host** (`pack.sh`: GNU tar + zstd, excluding caches/logs/clones/creds, newest 3 kept locally) into `/mnt/backup/dkr/offsite/`, then runs `rclone sync` of only `offsite/` + `pve-templates/` + each host's `pgdump/` to Proton (encrypted, in ≤25-min passes). Packing first turns thousands of small files into a few large objects, which the Proton API handles without the 502/422/empty-upload churn that stopped the old whole-tree sync from finishing. The same `pack` step also archives the owner's CAD library (laptop `~/cad`, synced every 15 min to `tankz3/3d-prints`, mounted read-only on kdk-dkr-04 at `/mnt/3d-prints`) as `offsite/3d-prints-YYYY-MM-DD.tar.zst` with its own small exclude list (`*.log`, `__pycache__`, `.~lock*`, `.~*.lock`), so it ships with the host archives ([[cad-library]]). `backup-verify` checks all five pg dumps are present + gzip-valid for the day, that tonight's `3d-prints` archive exists and passes `zstd -t`, and warns if the laptop's last sync (`.3d-prints-last-sync`, copied by `pack`) is older than 14 days.

DMZ data that lives on `/mnt/appdata` instead of `/opt/kdk-lab` (Seerr, Wizarr, Libreseerr, Jellyfin metadata, Audiobookshelf podcasts) is **not** in the nightly rsync or offsite: it is covered by tankz3 snapshots only. The Jellyfin metadata and podcasts are regenerable (re-scan / re-download from the feeds), so that is deliberate.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/backup-dkr-01/compose.yaml` | Jellystat + wger pg dumps + rsync of `/opt/kdk-lab` to the NAS. `pgdump-wger` joins the external `wger_backend` network: if the wger stack is down, the whole dkr-01 stage fails |
| `stacks/backup-offsite/compose.yaml` | separate stage: `pack` service builds per-host `tar.zst`, then `offsite` service `rclone sync`s `offsite/` + `pve-templates/` + `*/pgdump/` to Proton (crypt), `--max-duration 25m` per pass, writes `.offsite-status` |
| `stacks/backup-offsite/pack.sh` | builds `/mnt/backup/dkr/offsite/<host>-YYYY-MM-DD.tar.zst` per host (excludes caches/logs/clones/creds/pgdump), plus `3d-prints-YYYY-MM-DD.tar.zst` from `/src/3d-prints` (ro bind of dkr-04 `/mnt/3d-prints`), keeps newest 3 of each |
| `stacks/backup-dmz/compose.yaml` | Authelia + Immich + Tandoor pg dumps + rsync of `/opt/kdk-lab` (excludes live Postgres dirs, certs, clones, Jellyfin metadata/cache/transcodes, `audiobookshelf/podcasts`) |
| `/opt/kdk-lab/backup/rclone/rclone.conf` | `proton` + `proton-crypt` remotes |
| Procedure `nightly-backups` | schedule `0 20 3 * * *` (03:20 daily, `Cron` format) |

Restore: rsync/untar back from `/mnt/backup`, `psql < dump`.

**PBS job for VM 320 (kdk-log-01).** The nightly Proxmox → PBS (`kdk-pbs-01`)
vzdump job on kdk-hyp-01 must include VM 320: if its selection is "all", it is
picked up automatically; otherwise the owner adds it (Datacenter → Backup → the
job → Selection), mode `snapshot`. Loki's chunks and
index are in that disk image; a restore loses at most the day since the last dump. Migration tars remain under `tankz3/backup/migrate` until parity is confirmed.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `proton-mirror-crypt` (kdk-ops) | rclone crypt password | 🔴 purge-on-change | rotating it orphans every object — `rclone purge` the Proton target first |
| `authelia-postgres`, `immich-postgres` | pg_dump auth | 🟢 self-service | rotate with the DB, then redeploy |
| `jellystat` (postgres_password) | pg_dump auth for Jellystat | 🟢 self-service | rotate with the DB, then redeploy `jellystat` + `backup-dkr-01` |
| `wger-postgres` (password) | pg_dump auth for wger | 🟡 attended | rotate with the DB ([[wger]]), then redeploy `wger` + `backup-dkr-01` |
| `authelia-app-secrets` (`tandoor_postgres_password`) | pg_dump auth for Tandoor | 🟢 self-service | rotate with the DB, then redeploy `tandoor` + `backup-dmz` |

## Offsite status (2026-10-30)

Encrypted offsite to Proton runs **every night** as the `backup-offsite` stage of the `nightly-backups` Procedure — there is **no** profile gate. The stage now packs before it uploads: the `pack` service writes one `tar.zst` per host into `/mnt/backup/dkr/offsite/`, then `offsite.sh` runs `rclone sync` of only `offsite/` + `pve-templates/` + `*/pgdump/` in passes capped at `--max-duration 25m` each (Proton kills long-lived token refreshes, so each pass logs in fresh and runs short), then writes `/mnt/backup/dkr/.offsite-status` with rclone's exit code and always exits 0 so the verify stage still runs.

**Why the change:** the old whole-tree `rclone sync` never finished a night — thousands of churny small files against Proton's flaky API alternated `rc=10` (hit `--max-duration`) and `rc=2` (API errors: `502` block uploads, `422 already exists`, `400 empty`, "no response" on listings, plus `Can't follow symlink` on proton-bridge sockets). Packing cuts the Proton object count by ~3 orders of magnitude, so a clean night should reach `rc=0` in one or two passes. `rc=0` in `.offsite-status` is the signal it is fully in sync.

```bash
ssh kdkadmin@10.1.20.20 'sudo ls -la /mnt/backup/dkr/offsite/'   # today's per-host <host>-YYYY-MM-DD.tar.zst
ssh kdkadmin@10.1.20.20 'cat /mnt/backup/dkr/.offsite-status'    # rc=0 = fully synced; rc=10 = pass hit --max-duration, resumes next night
```
