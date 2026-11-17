---
title: insta360-flat
host: kdk-dkr-04
stack: insta360-flat
tier: internal
status: building
storage: yes
secrets: [kdk-nas-01-insta360-upload-smb]
updated: 2026-11-16
tags: [homelab/stack, tier/internal, status/building]
---

# insta360-flat

Nightly one-shot on kdk-dkr-04 that turns raw Insta360 X4 captures on the NAS into
finished flat stills. Each 360° capture becomes two ordinary photos (front and back),
leveled from the camera's accelerometer, touched up with PhotoCraft and written to
`tankz3/insta360-flat/<YYYY-MM>-<trip>/`, which [[immich]] indexes as a read-only
external library and files into one album per trip. How to add a trip: [[imaging]].

**Related:** [[kdk-dkr-04]] · [[kdk-nas-01]] · [[immich]] · [[imaging]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Raw Insta360 trips → two flat stills per capture for Immich |
| Status | 🟡 Building — validated on kdk-dkr-04 against the real mounts 2026-11-16 (draft PR) |
| Host | [[kdk-dkr-04]] |
| Endpoint | none (no port, no UI) |
| Image | `kdk/insta360-flat:pc0.23.1-ff9.0.2-39`, built on the host from `stacks/insta360-flat/Dockerfile` (python 3.12 slim + BtbN FFmpeg n9.0.2 + PhotoCraft CLI 0.23.1, sha256-pinned) |
| Schedule | Komodo Procedure `insta360-flat`, 02:35 America/Chicago, `failure_alert` |
| Input | `/mnt/insta360-upload` → `/upload:ro` (NFS ro, export 237, mapall `insta360-upload` 3012) |
| Output | `/mnt/insta360-flat` → `/flat` (NFS rw, export 238, mapall `jmack` 3003:3001) |
| Runtime user | `3003:3001`, read-only rootfs, `cap_drop: ALL`, tmpfs `/tmp` 2 GB, 6 GB / 512 pids / 10 CPUs |
| Alerts | Pushover `insta360-flat failed` (any failed capture or a crash); Komodo failure alert for pre_deploy/deploy errors |
| Blast radius | No new stills; Immich keeps showing what is already there |

## Verify

```bash
ssh kdkadmin@10.1.20.22 'findmnt -rn -o TARGET,OPTIONS /mnt/insta360-upload /mnt/insta360-flat | cut -c1-40'
# /mnt/insta360-upload ro,noatime,vers=4.1,...
# /mnt/insta360-flat rw,noatime,vers=4.1,...
ssh kdkadmin@10.1.20.22 'sudo docker logs insta360-flat 2>&1 | tail -1'
# insta360-flat: trips=1 captures=326 made=0 skipped=326 deferred=0 failed=0 elapsed=1s
ssh kdkadmin@10.1.20.22 'sudo docker inspect -f "{{.State.ExitCode}}" insta360-flat'   # 0
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Deploy fails `/mnt/insta360-upload is not mounted read-only` (or `-flat ... read-write`) | NFS unit down, or mounted with the wrong mode | `systemctl status 'mnt-insta360\x2dupload.mount' 'mnt-insta360\x2dflat.mount'`; start the unit, run the Procedure |
| `ls /mnt/insta360-upload/<trip>`: `Permission denied` | files on the dataset not owned by 3012 and no `USER 3012` ACL entry: the export squashes every client to 3012 with no supplementary groups, so `group@` never matches | `sudo midclt call filesystem.getacl /mnt/tankz3/insta360-upload \| jq -c '[.acl[]\|[.tag,.id]]'` on the NAS; fix in [[imaging]] ("Upload ACL") |
| `FAILED <file>: no Insta360 trailer / 0x300 motion record` | truncated `.insp` (copy cut short); it is not rendered unleveled | compare the size with the camera/laptop copy; re-copy; the next night retries it |
| `FAILED <file>: ffmpeg exit ...` | corrupt raw | the log line; re-copy the file; the next night retries it |
| `deferred <file>: ctime ...` | the raw changed less than 15 min before the run (copy may still be running) | nothing; the next run takes it (or run the Procedure later). A NAS-side `chmod`/`setacl`/`chown` also resets ctime, so the whole trip defers for 15 min after one (seen 2026-11-16) |
| `FAILED <file>: photocraft-cli exit ...` | PhotoCraft (alpha) refused the image | run the same command by hand (README); keep the pin, report upstream |
| Stills named `..._<trip>_...` instead of a place | no `places.tsv`, or no row covers that date/time | `places.tsv` in the trip's upload folder ([[imaging]]) |
| A still looks wrong after a fix | existing stills are never redone | delete the two stills over the NAS (the job never deletes), run the Procedure |
| `Read-only file system` | a tool wrote outside `/tmp` | add a tmpfs path in `compose.yaml` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[kdk-nas-01]] `tankz3/insta360-upload` + `tankz3/insta360-flat` over the storage net | pre_deploy refuses to run |
| Needs | `/etc/kdk/pushover.env` on the host | failures are only in the container log |
| Needed by | [[immich]] external library "Insta360" + `immich-albums` (02:10, dmz-01) | no new trip photos in Immich |

## Observability

Container log only (json-file, read by Alloy): one `ok <file> -> <name>[ab].jpg <secs>` line
per made capture, `FAILED ...` lines, and the summary line. No dashboard, no metrics.

## Architecture

```text
SMB //10.1.3.5/insta360-upload (user insta360-upload)
  -> tankz3/insta360-upload/<Trip>/IMG_*.insp|jpg (+ optional places.tsv)
  -> NFS ro (export 237) -> kdk-dkr-04 /mnt/insta360-upload
  -> insta360-flat (02:35): burst collapse, level, v360 stereographic, PhotoCraft, JPEG q92
  -> NFS rw (export 238) -> tankz3/insta360-flat/<YYYY-MM>-<trip>/
  -> NFS ro (export 236) -> kdk-dkr-dmz-01 /mnt/insta360-flat -> Immich library + albums
```

Bridge network (egress only for Pushover). No inbound.

## Configuration

What `flatten.py` does, in order (the laptop scripts in `~/cad/tools/imaging` are the
reference; the maths and the ffmpeg/PhotoCraft arguments are unchanged):

1. **Trips** = folders directly under `/upload` holding at least one `.insp`/`.jpg`
   (anything else, `.insv`/`.lrv` video included, is ignored; files not named
   `IMG_<date>_<time>_<nn>_<seq>` are logged and skipped).
2. **Output folder** = `<YYYY-MM of the earliest capture>-<trip lowercased, runs of
   non-alphanumerics → '-'>` (`Maine` → `2025-08-maine`).
3. **Bursts**: 3 or more captures sharing `IMG_<date>_<time>` (same second) keep only the
   lowest sequence number; a pair in the same second is two shots and both are kept.
   **Fresh raws** (ctime under 15 min: SMB copies keep the camera mtime, so ctime is the
   copy time) are deferred to the next run: logged, counted as `deferred`, not a failure.
4. **Two stills per capture**: `a` = heading 0, elevation −5; `b` = heading 180,
   elevation −15. Leveled from the `.insp` accelerometer record; stereographic, 95°
   horizontal (an `.insp` without the trailer or its motion record, i.e. truncated, FAILS
   instead of rendering unleveled; camera-stitched `.jpg` render unleveled); 3200×2400, or 1600×1200 when the source is under 8000 px wide; lossless
   PNG, then PhotoCraft (`shadowsHighlights 25/15`, `autoContrast`, `vibrance 18/4`,
   `smartSharpen 60/1/10`, `--no-gpu`) → JPEG quality 92.
5. **Names**: `YYYY-MM-DD_HHMM_<place>_<seq><a|b>.jpg`; `<place>` from the trip's
   `places.tsv` (slugified, so a slug can never add `/` or `..`), else the trip slug.
6. **Idempotent**: a still that exists non-empty is skipped. New stills are written to
   `.<name>.part` in the destination and renamed. Nothing in `/upload` is written; nothing
   in `/flat` is deleted.
7. Summary line `trips= captures= made= skipped= deferred= failed=`; any failure → Pushover + exit 1.

| Setting | Where | Default |
|---|---|---|
| `WORKERS` | `compose.yaml` env | `3` parallel captures (measured on the full Maine run: 34 min for 326 captures, ~6.3 s per capture effective; cgroup memory peak at the 6 GB limit, page cache of the NFS reads included, no OOM kill; 128 pids) |
| `PLACES_DIR` | env (testing only) | unset; `<dir>/<trip>.tsv` overrides `/upload/<trip>/places.tsv` |
| `FISHEYE_FOV` | env | `200` (per-lens FOV of the dual-fisheye source) |
| `SETTLE_S` | env | `900`: raws whose ctime is younger are deferred |
| Tool versions | `Dockerfile` ARGs + image tag in `compose.yaml` | PhotoCraft 0.23.1, FFmpeg n9.0.2-39 (BtbN `autobuild-2026-10-31-12-59`) |

**Bumping a tool**: take the sha256 from the release's own checksum file (PhotoCraft
`SHA256SUMS.txt`, BtbN `checksums.sha256`), change the ARGs **and** the image tag, rerun the
validation in [[imaging]]. Pin BtbN month-end builds only: daily autobuilds are deleted after
about two weeks.

## Secret Rotation

No secrets in the stack. The SMB upload password (`kdk-ops/kdk-nas-01-insta360-upload-smb`)
belongs to the NAS user `insta360-upload`; rotate it as in [[imaging]].

## User Guide

Copy a trip's raws into a new folder on the `insta360-upload` share; the stills appear in
Immich as an album named after the output folder, usually within two nights (this job at
02:35, then Immich's 02:10 album run the next night). Details: [[imaging]].
