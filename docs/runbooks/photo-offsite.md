---
title: Photo originals offsite (Immich + Insta360 → Proton + B2)
updated: 2026-11-19
---

# Photo originals offsite

The phone originals Immich stores on the NAS and the finished Insta360 stills go to Proton
Drive every night, **file by file** through the existing rclone crypt remote, as their own
stage (`photos`) of the `nightly-backups` procedure, and then to a second offsite copy on
**Backblaze B2** (own crypt key, 90 days of versions, plus the Immich DB dumps): see
[§ B2](#b2). Each photo is uploaded once; a normal
night only moves the new ones. `backup-verify` checks the result and the Grafana
**Backups / Nightly backups** dashboard shows it.

**Related:** [[backups]] · [[immich]] · [[kdk-dkr-04]] · [[kdk-nas-01]] · [CAD library](cad-library.md) (same NAS → dkr-04 → Proton pattern)

```text
dmz-01 Immich ──NFS rw──> tankz3/appdata/immich/{library,upload,profile}   (thumbs/, encoded-video/, backups/ NOT shipped)
                                   │  NFS ro, mapall=nobody, only 172.16.30.52
insta360-flat worker (dkr-04, 02:35) ──rw──> tankz3/insta360-flat ── same dkr-04 mount, bound :ro here
                                   ▼
kdk-dkr-04  backup-photos (after backup-offsite, before backup-verify)
            guard (NFS? marker? non-empty? >= 50 % of last night?) ──> rclone sync --size-only --max-delete 100
            4 × 25-min passes a night ──> proton-crypt:photos/immich/... + proton-crypt:photos/insta360-flat/...
            then B2: 2 × 60-min passes ──> b2photoscrypt:photos/... (+ immich-db/: the Immich dumps)
            writes /mnt/backup/dkr/.photos-status-{proton,b2} ──> backup-verify (dkr-01) ──> Pushover
            logs backup-manifest kind=photos ──> Loki ──> Grafana "Nightly backups"
```

| Item | Value |
|---|---|
| Stack | `stacks/backup-photos/` (`photos.sh`, `compose.yaml`), Komodo stack `backup-photos` on kdk-dkr-04, `deploy = false` (run by the procedure) |
| Stage | `photos` in `nightly-backups`, between `offsite` and `verify` |
| Sources | `/mnt/immich/{library,upload,profile}` (new ro NFS mount of `tankz3/appdata/immich`, unit `hosts/kdk-dkr-04/systemd/mnt-immich.mount`) and `/mnt/insta360-flat` (dkr-04's rw mount for the [[insta360-flat]] worker, unit `hosts/kdk-dkr-04/systemd/mnt-insta360\x2dflat.mount`, bound read-only here) |
| NAS export | `/mnt/tankz3/appdata/immich`, hosts `172.16.30.52` only, read-only, `mapall` = `nobody`/`nogroup` |
| Destination | `proton-crypt:photos/immich/{library,upload,profile}/...`, `proton-crypt:photos/insta360-flat/<trip>/...` (same crypt key as every other backup: 1Password `kdk-ops` / `proton-mirror-crypt`) |
| Destination 2 (B2) | `b2photoscrypt:photos/...` and `b2photoscrypt:immich-db/immich-DATE.sql.gz`: private B2 bucket, own crypt key (1Password `kdk-ops` / `b2-photos-crypt`), key `kdk-ops` / `b2-photos-app-key`, host file `/etc/kdk/backup-photos-b2/b2.env` ([§ B2](#b2)) |
| Status | `/mnt/backup/dkr/.photos-status-proton` (`rc= ts= date= passes= guard= last_ok= seed_start= crashes=`), `.photos-status-b2` (same + `db_rc= db_guard= db_last_ok=`), guard counts `/mnt/backup/dkr/.photos-state` |
| Checks | `backup-verify` section 6; `storage-health-dkr04` `NFS_MOUNTS=... immich`; Loki `BackupFileShrank` |

## Design

- **File by file, not packed.** The host archives are packed into `tar.zst` because Proton
  chokes on thousands of small churny files. Photos are the opposite: large, immutable and
  only ever added. A pack would re-upload the whole, growing library every night; a sync
  uploads each file once.
- **Own stage after `offsite`.** Same host and the same Proton session
  (`/opt/kdk-lab/backup/rclone/rclone.conf`, single-use refresh token): two rclone runs at
  once break it (`Code=10013`). Its own stack also keeps a photo problem out of the host
  archive step and gives it its own row on the dashboard.
- **Narrow read-only access.** dkr-04 gets one new export, the `immich` directory only
  (not `tankz3/appdata`, which holds every DMZ app's data), read-only, as `nobody` (Immich
  writes its files 644, the dataset ACL has `everyone@` read). The container binds only
  `library`, `upload` and `profile`. Insta360 reuses the `/mnt/insta360-flat` mount
  that the [[insta360-flat]] worker already has on dkr-04 (`nfsvers=4.1,noatime,_netdev`, rw,
  export `mapall` jmack), bound `:ro` into this container: no new export.
- **`--size-only`.** Proton cannot update modification times, and originals never change in
  place (an edit is a new file), so same path + same size = same file. It also saves a
  per-file modtime read on every listing.
- **Consistency without stopping Immich.** Originals are written once and never rewritten.
  A file that appears during the run is picked up the next night; one moved from `upload/` to
  `library/` mid-run shows up in one place or the other and settles the next night; the size
  check re-sends anything that changed.
- **The insta360-flat worker** writes into the same tree at 02:35 (a fresh ~300-capture trip
  takes ~25 min, so it is normally done before `nightly-backups` starts at 03:20 and long
  before the `photos` stage). It writes each still as `.<name>.jpg.part` in the trip folder
  and renames it into place. `photos.sh` never ships `.*.part` files (first filter rule), so
  if a big trip overlaps the stage, half-written stills are skipped and the finished ones
  upload on the next pass (each pass re-lists) or the next night. A new trip also raises the
  file count, so the shrink guard never trips on it. The DB
  dump (`immich-DATE.sql.gz`, stage-dmz, earlier the same night) can be a few minutes older
  than the files: a restore then has a handful of files the DB does not know (Immich
  Administration → Repair lists them) and no DB rows without a file, since the dump comes
  first. Stopping Immich nightly would buy nothing more.

### Deletion safety

`rclone sync` deletes offsite what is gone at the source, so a dead mount must never look
like "everything was deleted". In order:

1. **Guard, per source, before rclone runs.** A source is refused when a bind is not NFS in
   `/proc/self/mountinfo` (a dead host mount leaves Docker binding the bare, empty local
   dir), its marker is missing (`library/.immich`, which Immich writes; at least one trip
   folder for Insta360), it has no files, or it has fewer than 50 % of the files of the last
   accepted night. A refused source gets a `- /<source>/**` filter and rclone never deletes
   destination files its filters exclude, so its Proton copy stays exactly as it was.
   `backup-verify` fails the night.
2. **`--max-delete 100`.** More deletes than that in one run is a fatal rclone error (exit 7);
   nothing past the limit is deleted; `backup-verify` fails the night.
3. **rclone's own rules.** Deletes run after the transfers and are skipped entirely when a
   pass had any I/O error, so a pass cut short by the 25-min cap deletes nothing.
4. **Missing Immich mount = no container.** The Immich binds use
   `create_host_path: false`: with `/mnt/immich` unmounted the subdirectories do not exist
   and the container refuses to start (stage fails, no fresh status, verify fails "status
   stale").

**`sync`, not `copy` (owner decision 2026-11-19, both remotes).** Immich keeps a deleted photo in its trash
for 30 days, then removes the file; the tankz3 snapshots keep it 2 more weeks; the B2
copy keeps 90 days of versions after that. With `sync`, Proton is a mirror: a deliberate delete leaves
Proton the night the trash purges it. `copy` never deletes, so Proton would keep every
deliberately deleted photo forever, and every file the storage template renames (editing a
photo's date moves it) would exist twice. Until B2 lands, a photo purged from the Immich
trash is recoverable from NAS snapshots for 2 weeks and from B2's hidden versions for 90 days
(`PHOTOS_MODE` applies to both remotes; `copy` stays available as a by-hand override).

### Proton seeding and what "success" means

The first run uploads ~13 GB in ~5,000 files (2026-11-16: Immich 12 GB / 4,422 files incl.
`upload` 8 MB and `profile`; Insta360 1.2 GB / 652 files). Last night's offsite moved ~6 GB
in about an hour, so expect two to four nights at 4 × 25-min passes. Every pass and every
night resumes where the last one stopped (files already on Proton are skipped). After that, a
night is the day's new photos.

| `.photos-status-proton` | verify says |
|---|---|
| `rc=0 guard=ok` | OK (`photos in sync` in the all-clear) |
| no full sync yet (`last_ok=0`), < 14 days since the first run | **WARN** `first upload still seeding (day N, rclone exit=10 ...)` |
| no full sync after 14 days | **FAIL** `first upload not complete after Nd` |
| after the seed: not in sync tonight (cap or Proton blip), full sync < 3 days ago | **WARN** `not fully synced tonight ... last full sync Nh ago` |
| after the seed: not in sync and no full sync for 3 days | **FAIL** |
| `rc=91` (the last pass crashed: rclone Go panic) or `crashes=N` > 0 | same rows as a cap (WARN while seeding / full sync < 3 days, then FAIL); the message adds `N crashed: rclone Go panic, retried`. See [Troubleshooting](#troubleshooting) |
| `guard=refused:...` | **FAIL** (the refused source was left out, its offsite copy is kept) |
| `rc=7` (`--max-delete` hit) or `rc=2` (usage error, no panic) | **FAIL** |
| file missing or older than 6 h | **FAIL** |

A big batch later (the second phone's first backup) behaves like the seed: one or two nights
of WARN `not fully synced tonight`, then OK.

## Order

Steps 1–7 are the Proton rollout (done 2026-11-19). The B2 copy has its own steps
B1–B5 (before its merge) and A1–A4 (after): [§ B2](#b2).

**Before merge** (owner):

1. NAS: the read-only export.
2. kdk-dkr-04: install + enable the mount unit, verify the reads.
3. Proton: check the free space.

**After merge** (owner):

4. ResourceSync, deploy `storage-health-dkr04`.
5. First run by hand: dry run, then a real Deploy of `backup-photos` during the day.
6. The next mornings: verify + dashboard until `rc=0`.
7. Restore test (gates the second phone's enrolment).

**Never merge before step 2 verifies:** `storage-health` pages `/mnt/immich MISSING` every
hour and the photos stage fails every night.

## 1. NAS export (owner, `ssh kdkadmin@10.1.3.5`)

```sh
sudo midclt call group.query '[["group","=","nogroup"]]' | jq -c '.[0] | {gid,group}'      # {"gid":65534,"group":"nogroup"}
sudo midclt call sharing.nfs.query '[["path","^","/mnt/tankz3/appdata"]]' | jq -c '.[] | {id,path,hosts,ro}'   # only id 98 (/mnt/tankz3/appdata, .50 + .51)
sudo midclt call sharing.nfs.create '{"path":"/mnt/tankz3/appdata/immich","comment":"Immich originals, read-only to kdk-dkr-04 for backup-photos (docs/runbooks/photo-offsite.md)","hosts":["172.16.30.52"],"ro":true,"mapall_user":"nobody","mapall_group":"nogroup"}'
sudo midclt call sharing.nfs.query '[["path","=","/mnt/tankz3/appdata/immich"]]' | jq -c '.[0] | {id,hosts,ro,mapall_user,mapall_group}'
sudo showmount -e localhost | grep immich        # /mnt/tankz3/appdata/immich 172.16.30.52
```

If TrueNAS refuses the sub-directory path, stop there and report it: do **not** export
`/mnt/tankz3/appdata` to dkr-04 instead (every DMZ app's data is in it). The fallback is a
child dataset `tankz3/appdata/immich` (needs an Immich stop and a copy), which is a separate
change. No new snapshot task: `tankz3/appdata` is already snapshotted hourly (2 days) and
daily (2 weeks), recursively.

## 2. kdk-dkr-04 mount (owner)

From a repo checkout of this branch on the laptop:

```sh
scp hosts/kdk-dkr-04/systemd/mnt-immich.mount kdkadmin@10.1.20.22:/tmp/
ssh kdkadmin@10.1.20.22
sudo install -m 644 /tmp/mnt-immich.mount /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now mnt-immich.mount && findmnt /mnt/immich
rm /tmp/mnt-immich.mount
ls /mnt/immich                                    # backups encoded-video library profile thumbs upload
sudo find /mnt/immich/library /mnt/immich/upload /mnt/immich/profile -type f | wc -l     # same as dmz-01 (below)
sudo find /mnt/immich/library -type f ! -name .immich -print -quit | sudo xargs -d '\n' sha256sum   # a hash, not "Permission denied"
sudo touch /mnt/immich/.ro-test                   # must fail: Read-only file system
findmnt /mnt/insta360-flat                        # nfs4 172.16.30.2:/mnt/tankz3/insta360-flat (the insta360-flat worker's rw mount)
```

The count to compare with: `ssh kdkadmin@192.168.191.20 'sudo find /mnt/appdata/immich/library /mnt/appdata/immich/upload /mnt/appdata/immich/profile -type f | wc -l'`.
`sudo find` on dkr-04 reads as `nobody` through the export (mapall), so a matching count and
a hash prove the backup can read every file.

The unit has no `nofail` (same as the other dkr-04 mounts): enable it only after step 1. A
rebuild enables it from cloud-init on its own `runcmd` line (`|| true`).

## 3. Proton free space (owner, before merge)

The first upload adds ~13 GB, and every phone added later adds its whole library. Outside the
nightly window (03:20 until the `photos` stage ends), on kdk-dkr-04:

```sh
sudo docker run --rm -v /opt/kdk-lab/backup/rclone:/config rclone/rclone:1.76.0 --config /config/rclone.conf about proton:
```

`Free` should comfortably exceed 13 GB plus the second phone's library. If the backend
reports nothing useful, read the storage meter in the Proton web app instead. (Like the
restore commands, this uses the live session and rotates its token in place, which is normal.)

## 4. Merge, storage-health

Merge. After the gitops-sync ResourceSync (≤ 15 min, or run it): Komodo → Stacks →
`storage-health-dkr04` → Deploy. Log line:
`storage-health OK (172.16.30.2, mounts: media backup linux-tools 3d-prints insta360-upload insta360-flat immich)`.
`backup-photos` and `backup-verify` are deployed by the nightly procedure; nothing else to deploy.

## 5. First run by hand (during the day)

**5a. Dry run** from a repo checkout on kdk-dkr-04 (not the Komodo clone). It runs the
guard, then `rclone --dry-run` (it lists Proton, uploads and deletes nothing) and writes no
status or state:

```sh
ssh kdkadmin@10.1.20.22
git clone --depth 1 -b main https://github.com/BigH3ater/kdk-lab-dkr.git /tmp/kdk-photos && cd /tmp/kdk-photos/stacks/backup-photos
sudo docker compose run --rm --no-deps -e PHOTOS_DRY_RUN=1 -e PHOTOS_MAX_PASSES=1 photos 2>&1 | grep -v 'Skipped copy as --dry-run' | tail -20
cd / && rm -rf /tmp/kdk-photos
```

Expect two `backup-manifest ... kind=photos ... guard=ok` lines (`series=immich files=~4422`,
`series=insta360-flat files=652`), no `REFUSED`, then
`photos rclone exit=0 remote=proton passes=1 ... guard=ok mode=sync` and
`photos: dry run, /data/.photos-status-proton not written`. Its manifest lines reach Loki like a real run's
(the `*-run-*` container is ignored by the step panels).

**5b. Real run.** Komodo → Stacks → `backup-photos` → **Deploy**, at least 2 h before 03:20.
It runs up to 4 × 25 min. Then:

```sh
ssh kdkadmin@10.1.20.22 'sudo docker logs backup-photos-photos-1 2>&1 | grep -E "^backup-manifest|^photos|NOTICE" | tail -20'
ssh kdkadmin@10.1.20.22 'sudo cat /mnt/backup/dkr/.photos-status-proton /mnt/backup/dkr/.photos-state'
```

- `photos pass N: remote=proton rclone exit=10 secs=15xx` per pass while seeding, `exit=0`
  on the pass that finishes.
- `.photos-status-proton`: `rc=10 ... guard=ok last_ok=0 seed_start=<now> crashes=0` while
  seeding; `rc=0 ... last_ok=<time>` once complete. A pass logged
  `exit=91 ... raw_exit=2 crash=panic` crashed and the next pass resumed (see
  [Troubleshooting](#troubleshooting)).
- `.photos-state`: `immich_files=...`, `immich_bytes=...`, `insta360_files=652`, `insta360_bytes=...`.

## 6. The next mornings

- Pushover: `kdk-lab backup: warnings` with `photos/proton: first upload still seeding (day N ...)`
  until the seed completes, then `kdk-lab backup OK` ("photo originals in sync").
- Grafana **Backups / Nightly backups**: Files table rows `photos/immich` and
  `photos/insta360-flat` (host `kdk-nas-01`, kind `photos`; size = the whole tree; Proton
  `not synced` while seeding, then `on Proton`); Steps rows `backup-photos-photos-1` and
  `backup-photos-gate-1` exit 0; the **Photo originals** row (Photos on Proton, exit per
  pass, files per source, upload time).
- **No re-uploads:** the first night after `rc=0`, the last rclone stats line
  (`NOTICE: <done> / <total>, 100%, ...`, every 5 min and at the end) shows `0 B / 0 B` or
  only that day's new photos, and the pass takes minutes. If every
  night re-uploads everything, `--size-only` is not doing its job: report it.

## 7. Restore test (gate for the second phone)

From the laptop, with `offsite-mount.sh` (read-only crypt mount with the laptop's own Proton
session; the crypt key comes from 1Password at mount time and never touches the disk; see
[CAD library → Validate offsite from the laptop](cad-library.md#validate-offsite-from-the-laptop)).
Only after a night with `rc=0`.

```sh
offsite-mount.sh                                   # 1Password prompt, then: mounted ~/mnt/offsite
ls ~/mnt/offsite/photos/                           # immich  insta360-flat
ls ~/mnt/offsite/photos/immich/library/            # one folder per Immich user (storage template)

# one random phone photo older than 2 days, and one Insta360 still
P=$(ssh kdkadmin@10.1.20.22 'sudo find /mnt/immich/library -type f ! -name .immich -mtime +2 | shuf -n 1'); echo "$P"
I=$(ssh kdkadmin@10.1.20.22 'sudo find /mnt/insta360-flat -type f -mtime +2 | shuf -n 1'); echo "$I"

sha256sum "$HOME/mnt/offsite/photos/immich/${P#/mnt/immich/}" "$HOME/mnt/offsite/photos/insta360-flat/${I#/mnt/insta360-flat/}"
ssh kdkadmin@10.1.20.22 "sudo sha256sum '$P' '$I'"
                                                   # the same two hashes

# and it opens
cp "$HOME/mnt/offsite/photos/immich/${P#/mnt/immich/}" /tmp/restore-test-photo && xdg-open /tmp/restore-test-photo
rm /tmp/restore-test-photo
offsite-mount.sh -u                                # unmounted ~/mnt/offsite
```

Record the date, both paths and "hashes match" in the vault (`10.Homelab/TODO.README.md`,
Immich section) and only then enrol the second phone. Keep the mount short: `ls` deep into
`photos/` is one Proton API call per directory (see the CAD runbook's note on the 2026-11-16
CPU spike).

## Restore (real)

Never sync **from** Proton back over the live library; copy into a scratch folder and move
what you need. On kdk-dkr-04, outside the nightly window:

```sh
sudo mkdir -p /tmp/photo-restore
sudo docker run --rm -v /opt/kdk-lab/backup/rclone:/config -v /tmp/photo-restore:/out rclone/rclone:1.76.0 \
  --config /config/rclone.conf copy proton-crypt:photos/immich/library/<user>/<year>/<date>/ /out/ --transfers 2
```

- **A few photos:** copy them from `/tmp/photo-restore` to the laptop and upload them through
  the Immich app or web (Immich re-indexes them as new assets).
- **The whole library** (NAS lost): restore `proton-crypt:photos/immich/` into
  `/mnt/appdata/immich/` on dmz-01's NFS (`library/`, `upload/`, `profile/` with their
  `.immich` markers), `chown -R 1000:1000`, restore the DB from the newest
  `immich-DATE.sql.gz` ([[backups]], [[immich]]), start Immich, then run the thumbnail and
  video-transcode jobs (thumbs/ and encoded-video/ are not backed up; they regenerate).
- **Insta360:** `proton-crypt:photos/insta360-flat/<trip>/` back into `tankz3/insta360-flat`;
  the next 00:00 Immich scan and 02:10 album job pick them up.

Then `sudo rm -r /tmp/photo-restore`.

## Overrides (by hand, never by default)

| Situation | What to do |
|---|---|
| verify FAILs `guard refused:immich:shrank:N/M` after a real mass delete (someone emptied a big trash) | check in Immich that it was deliberate, then one run with `-e PHOTOS_ALLOW_SHRINK=1 -e PHOTOS_MAX_DELETE=<enough>` via `sudo docker compose run --rm --no-deps ... photos` (checkout as in 5a) |
| verify FAILs `rclone exit=7` (more than 100 deletes) after a legitimate rename (date edits, storage template change) | same, with `-e PHOTOS_MAX_DELETE=<enough>` |
| verify FAILs `guard refused:...:not-nfs` | the mount: `findmnt /mnt/immich /mnt/insta360-flat` on dkr-04; start the unit; the next night syncs normally |

## Troubleshooting

**`photos pass N: ... rclone exit=91 ... raw_exit=2 crash=panic`** (status `rc=91` or
`crashes=N`). rclone crashed with a Go panic. Seen 2026-11-19 (rclone 1.76.0): the 25-min
`--max-duration` deadline cancelled a Proton folder create in flight, and the Proton
library dereferenced the missing response. Signature in `docker logs backup-photos-photos-1`:

```
panic: runtime error: invalid memory address or nil pointer dereference
[signal SIGSEGV: segmentation violation ...]
github.com/rclone/go-proton-api.(*Client).CreateFolder(...)  go-proton-api@v1.0.4/link.go:91
github.com/rclone/Proton-API-Bridge.(*ProtonDrive).CreateNewFolder(...)
github.com/rclone/rclone/backend/protondrive.(*Fs).CreateDir ...
```

A Go panic exits 2, rclone's usage-error code, so photos.sh tells them apart by the
`panic:` / `fatal error:` line in that pass's output and records the pass as 91. Harmless
like a duration cut: deletes only run after a clean transfer phase, uploaded files stay,
`last_ok` does not move, the next pass (and night) resumes. Nothing to do unless it happens
every pass, night after night (verify then FAILs through the 3-day / 14-day rules): check
for a newer rclone with a Proton backend fix. A plain `exit=2` without `crash=panic` is a
real usage error (bad flag or config) and stops the night with a FAIL.

## Verify

| Check | Where | Pass |
|---|---|---|
| `findmnt /mnt/immich` | dkr-04 | `nfs4 172.16.30.2:/mnt/tankz3/appdata/immich ro,...,vers=4.1` |
| storage-health log | Komodo | `mounts: media backup linux-tools 3d-prints insta360-upload insta360-flat immich` |
| `cat /mnt/backup/dkr/.photos-status-proton` | dkr-04 | `rc=0 ... guard=ok` (after the seed) |
| `backup-verify` | Pushover / Komodo log | no `photos/proton` FAIL (WARN only while seeding) |
| Grafana Nightly backups | Files table, Photo originals row | `photos/*` rows `on Proton`, Photos on Proton `in sync` |
| `cat /mnt/backup/dkr/.photos-status-b2` | dkr-04 | `rc=0 ... guard=ok ... db_rc=0 db_guard=ok` (after the B2 seed) |
| `backup-verify` | Pushover / Komodo log | no `photos/b2` FAIL (WARN only while B2 seeds) |
| Grafana Nightly backups | Files table B2 column, Photos on B2, Immich DB on B2 | `on B2`, `in sync`, `in sync` |

## Rollback

1. Revert the PR, ResourceSync (removes the `photos` stage and the `backup-photos` stack;
   `immich` dropped from `storage-health-dkr04` `NFS_MOUNTS`), Deploy
   `storage-health-dkr04` **before** unmounting, or it pages MISSING.
2. kdk-dkr-04: `sudo systemctl disable --now mnt-immich.mount && sudo rm /etc/systemd/system/mnt-immich.mount && sudo systemctl daemon-reload`;
   `sudo rm /mnt/backup/dkr/.photos-status-proton /mnt/backup/dkr/.photos-state`.
3. NAS: `sudo midclt call sharing.nfs.delete <id>` (the id from step 1's query).
4. The Proton copy under `photos/` stays; the host offsite sync never touches it (outside its
   `--filter` set). Removing it is an owner decision: do not script a delete.

B2 alone: [§ B2 → Rollback](#rollback-b2-only).

## B2

The second offsite copy: the same photo originals (Immich `library/` + `upload/` +
`profile/`, the Insta360 export) **and the Immich DB dumps** in a private Backblaze B2
bucket, behind its **own** rclone crypt key. It is the copy that has to survive losing the
lab and the Proton account at once, so its restore works from the laptop with nothing but
1Password and rclone ([A4](#a4-restore-test-b2-gate-for-the-second-phone)).

```text
photos.sh (one container, after the Proton passes)
  guard (once, shared) ─> Proton passes (unchanged) ─> .photos-status-proton
                       └> B2: b2.env? ── no ──> rc=92 "not configured" ─> .photos-status-b2
                                  └ yes ─> DB pass: /data/kdk-dkr-dmz-01/pgdump/immich-*.sql.gz ─> b2photoscrypt:immich-db/
                                          photo passes (2 × 60 min) ─> b2photoscrypt:photos/{immich,insta360-flat}/
                                          ─> .photos-status-b2 (rc, guard, last_ok, seed_start, db_rc, db_guard, db_last_ok)
b2photoscrypt = crypt(b2photos:<bucket>), names + dirs encrypted; b2photos = B2, bucket-restricted key, no deleteFiles
```

| Item | Value |
|---|---|
| Bucket | private (`allPrivate`), default encryption SSE-B2, no Object Lock; name in 1Password `kdk-ops` / `b2-photos-app-key` field `bucket` |
| Lifecycle | `daysFromHidingToDeleting=90` (a deleted or overwritten file stays recoverable 90 days), `daysFromStartingToCancelingUnfinishedLargeFiles=7`, `daysFromUploadingToHiding=null`, prefix `""` |
| Key | `kdk-dkr-04-backup-photos`: this bucket only, `listBuckets listFiles readFiles writeFiles`; **no `deleteFiles`** (cannot erase versions), no bucket or key rights |
| 1Password (`kdk-ops`) | `b2-photos-app-key` (API Credential: `username` = key ID, `credential` = key, `bucket`, `bucket_id`); `b2-photos-crypt` (Password: `password`, `salt`) |
| 1Password (`Private`) | `b2-master-key` (API Credential: master key ID + key). Only for creating keys and bucket settings. **Never** in `kdk-ops`: 1Password Connect (every `op.env` host) can read `kdk-ops` |
| Host file | kdk-dkr-04 `/etc/kdk/backup-photos-b2/b2.env`, dir root 700, file root 600, lines `PHOTOS_B2_ACCOUNT=`, `PHOTOS_B2_KEY=`, `PHOTOS_B2_BUCKET=`, `PHOTOS_B2_CRYPT_PASSWORD=`, `PHOTOS_B2_CRYPT_SALT=` (plain values; photos.sh obscures the two crypt values in memory). Not under `/opt/kdk-lab`, so `backup-collect` never copies it into the backup tree |
| Layout | `b2photoscrypt:photos/immich/{library,upload,profile}/...`, `b2photoscrypt:photos/insta360-flat/<trip>/...`, `b2photoscrypt:immich-db/immich-YYYY-MM-DD.sql.gz` (the 15 the folder keeps) |
| Size | ~14 GB today (Immich 12.56 GB / 4,422 files, Insta360 1.26 GB / 652 files), plus the second phone's library once it is enrolled, plus up to 90 days of deleted/replaced versions |
| Helper | `stacks/backup-photos/b2-rclone.sh [--op] <rclone args>`: the same two remotes, from `b2.env` (dkr-04) or from 1Password (`--op`, laptop) |

### Design choices

- **Same container, after Proton, own subshell.** One guard decides for both remotes (a
  refused source is left out of both). Proton runs first with exactly its old flags and
  writes its status before B2 starts, so nothing on the B2 side can block or fail it. B2 runs
  whatever Proton's result was (exit 2/7, crash, cap), and a B2 failure only writes
  `.photos-status-b2`. The B2 variables are exported in the b2 subshell only: Proton's rclone
  never sees them, and B2 runs with `--config ""`, so it never reads or rewrites the Proton
  session file. The cost: the `photos` stage gets up to ~2 h longer while B2 seeds (normally
  minutes after that), and a Proton rclone that hung past its own caps would hold B2 too
  (never seen; the stage would then also hold `verify`, which pages "stale" the next night).
- **No `op inject`.** kdk-dkr-04 has no 1Password CLI and no `/etc/komodo/op.env` (none of
  its stacks render secrets; [[kdk-dkr-04]] holds only the periphery passkey and
  `/etc/kdk/pushover.env`). Adding Connect there would put a token that reads all of
  `kdk-cluster` + `kdk-ops` on the host for one credential. Instead the owner writes one
  host-managed file from 1Password, the lab's pattern for hosts without `op.env`
  ([rotate-a-secret](rotate-a-secret.md)). It is bound as a **directory**: before the
  hand steps Docker creates the empty directory and photos.sh skips B2 (`rc=92`); a
  single-file bind of a missing file would stop the container and with it Proton.
- **Comparison: size + modtime (rclone's default), not `--size-only`.** B2 stores the source
  modtime with each upload (`src_last_modified_millis`) and returns it in the listing, and
  crypt passes it through, so the check costs no extra API call and also catches a same-size
  change. Crypt has no usable hash, so a changed modtime means a re-upload (a new version; the
  old one is kept 90 days). A mass modtime change (a restore without `-p`) would re-upload
  everything once: more versions, no loss.
- **Caps.** No single session or panic constraint on B2, and it is fast, so: `--transfers 4
  --checkers 8 --fast-list` (one recursive listing, fewer API calls), passes of 60 min with
  `--cutoff-mode soft` (uploads in flight finish, so no half large files), 2 passes a night
  (`PHOTOS_B2_MAX_PASSES`), parts 48M × 2 per large file (memory stays far under the 1 GB
  limit). Seeding ~14 GB needs about 16 Mbit/s up to finish in one night; slower lines take
  another night (verify WARNs "seeding" for up to 14 days, like Proton).
- **Deletes are hides.** `hard_delete=false` and a key without `deleteFiles`: `rclone sync`
  deleting a file calls `b2_hide_file` (needs `writeFiles`); overwriting uploads a new version.
  rclone calls `b2_delete_file_version` only for `--b2-hard-delete`, `cleanup`, `purge` or
  deleting a `--b2-versions` name, none of which the nightly runs. With this key those fail
  `unauthorized`, so a compromised kdk-dkr-04 can hide or overwrite files but cannot erase
  the 90-day history. What breaks without `deleteFiles`: `rclone cleanup` / `backend
  cleanup-hidden` (old versions) — the lifecycle rule does that instead; unfinished large
  uploads are cancelled by rclone (`b2_cancel_large_file` needs only `writeFiles`) and, if one
  is left behind, by the lifecycle's 7-day rule. `rclone sync` needs no bucket rights: with a
  key restricted to one bucket rclone marks that bucket as existing and never calls
  `b2_create_bucket` ([A1](#a1-dry-run-from-a-checkout-on-kdk-dkr-04) proves it).
- **The Immich DB dumps.** `backup-dmz` writes `immich-YYYY-MM-DD.sql.gz` into
  `/mnt/backup/dkr/kdk-dkr-dmz-01/pgdump` earlier the same night and keeps 15. **Proton
  already has them:** `backup-offsite` syncs `/data` (= `/mnt/backup/dkr`) with
  `--filter "+ /*/pgdump/**"`, so they land in `proton-crypt:kdk-dkr-dmz-01/pgdump/`
  ([[backups]]). B2 gets them from the same folder (this container already binds
  `/mnt/backup/dkr` at `/data`): one pass (15-min cap) before the photos, only
  `immich-*.sql.gz` (not authelia/tandoor), same mode (`sync`, so B2 follows the 15-kept
  rotation and the older ones are hidden, kept 90 days), `--max-delete 5`, and refused
  (B2 copy untouched) when the folder holds no immich dump.
- **Own crypt key.** Not `kdk-ops/proton-mirror-crypt`: one leaked key opens one copy. The
  crypt password and salt are generated in 1Password in [B3](#b3-crypt-password-and-salt-in-1password);
  file and directory names are encrypted (`standard`, base32), like Proton.

### What verify and the dashboard show

`backup-verify` checks `.photos-status-b2` with the same rules as Proton (own seeding window
from B2's own `seed_start`; see the [table above](#proton-seeding-and-what-success-means)),
plus:

| `.photos-status-b2` | verify says |
|---|---|
| `rc=92` (no `b2.env`, or a field empty) | **FAIL** `photos/b2: not configured (...)` |
| `db_guard=no-dumps` / `no-dir` | **FAIL** `Immich DB dumps not sent` (B2 copy kept) |
| `db_rc=2` / `7` | **FAIL** |
| `db_rc` other than 0, a good DB sync < 3 days ago (or the first 3 days of B2) | **WARN** |
| `db_rc` other than 0, no good DB sync for 3 days | **FAIL** |

Grafana **Backups / Nightly backups**: the Files table has a **B2** column for the photo rows
(`on B2`, `not synced`, `left out (guard)`, `not configured`); the Photo originals row has
**Photos on B2**, **Photos on B2: exit per pass per night**, **Immich DB on B2**, **Immich DB
on B2 per night**, and the upload-time panel is split per remote. Log lines:
`photos db: remote=b2 rclone exit=N files=N secs=S guard=ok`,
`photos pass N: remote=b2 rclone exit=N secs=S`, `photos rclone exit=N remote=b2 ...`,
`photos: b2 not configured (no-env-file), skipped: ...`.

### If this merges before the hand steps

Nothing breaks: Docker creates the empty `/etc/kdk/backup-photos-b2` on the next nightly,
Proton runs exactly as before, photos.sh logs `photos: b2 not configured (no-env-file)` and
writes `.photos-status-b2` with `rc=92`, and `backup-verify` **FAILs every night**
(`photos/b2: not configured`, Pushover priority 1) until B4. That page is the only effect,
which is why the order is "B1–B5 first, then merge".

### Before merge (owner, laptop)

Bash on the laptop, in one terminal session (the variables carry from step to step).
Reads and writes use the owner's own 1Password session (`env -u OP_SERVICE_ACCOUNT_TOKEN`);
in a Claude Code session prefix each `op` write with `!`. Nothing below prints a secret.

#### B1. Master key into 1Password (`Private`)

Backblaze web console → **Application Keys** → **Master Application Key**. If a master key
already exists and is in use somewhere, do **not** generate a new one (that invalidates the
old one): use the existing key from wherever it is kept. Otherwise **Generate New Master
Application Key** and paste the two values straight into the 1Password app: vault
**Private**, new **API Credential** item `b2-master-key`, `username` = keyID,
`credential` = applicationKey, `hostname` = `api.backblazeb2.com`. (Not `kdk-ops`.) Then:

```bash
R() { env -u OP_SERVICE_ACCOUNT_TOKEN op read "$1"; }
AUTH=$(printf 'user = "%s:%s"\n' "$(R op://Private/b2-master-key/username)" "$(R op://Private/b2-master-key/credential)" \
  | curl -sf -K - https://api.backblazeb2.com/b2api/v4/b2_authorize_account)
API=$(jq -r .apiInfo.storageApi.apiUrl <<<"$AUTH"); ACCT=$(jq -r .accountId <<<"$AUTH"); echo "$API"   # https://apiNNN.backblazeb2.com
b2api() { curl -sf -K <(printf 'header = "Authorization: %s"\n' "$(jq -r .authorizationToken <<<"$AUTH")") \
  -H 'Content-Type: application/json' -d "$2" "$API/b2api/v4/$1"; }
```

`curl -K -` / `-K <(...)` keeps the key and token off the command line (and out of `ps`).

#### B2. Bucket with the 90-day rule, and the nightly key

```bash
BUCKET=kdk-photos-$(openssl rand -hex 4); echo "$BUCKET"      # globally unique, no personal data in the name
b2api b2_create_bucket "$(jq -nc --arg a "$ACCT" --arg b "$BUCKET" '{accountId:$a, bucketName:$b, bucketType:"allPrivate",
  lifecycleRules:[{fileNamePrefix:"", daysFromUploadingToHiding:null, daysFromHidingToDeleting:90, daysFromStartingToCancelingUnfinishedLargeFiles:7}]}')" \
  > /tmp/b2-bucket.json; jq '{bucketId, bucketName, bucketType, lifecycleRules, defaultServerSideEncryption, fileLockConfiguration}' /tmp/b2-bucket.json
BID=$(jq -r .bucketId /tmp/b2-bucket.json); rm /tmp/b2-bucket.json; echo "$BID"     # bucket metadata only, no secret
```

Check the printed JSON: `bucketType: "allPrivate"`, the one lifecycle rule exactly as sent,
`defaultServerSideEncryption.mode: "SSE-B2"` (Backblaze's default), file lock disabled. If
B2 rejects the request, stop and report the error (do not loosen the rule).

The key: this bucket only, the four capabilities, **no `deleteFiles`**:

```bash
KEY=$(b2api b2_create_key "$(jq -nc --arg a "$ACCT" --arg b "$BID" '{accountId:$a, keyName:"kdk-dkr-04-backup-photos",
  bucketIds:[$b], capabilities:["listBuckets","listFiles","readFiles","writeFiles"]}')")
jq '{keyName, capabilities, bucketIds}' <<<"$KEY"        # no secret in this output
env -u OP_SERVICE_ACCOUNT_TOKEN op item create --vault kdk-ops --category 'API Credential' --title b2-photos-app-key \
  username="$(jq -r .applicationKeyId <<<"$KEY")" "credential[password]=$(jq -r .applicationKey <<<"$KEY")" \
  hostname=api.backblazeb2.com "bucket[text]=$BUCKET" "bucket_id[text]=$BID" \
  "notesPlain=backup-photos B2 key (kdk-dkr-04 /etc/kdk/backup-photos-b2/b2.env). Bucket-restricted, no deleteFiles. docs/runbooks/photo-offsite.md#b2" >/dev/null
unset KEY
```

The application key is shown by B2 only once, so it goes into 1Password in the same command.

#### B3. Crypt password and salt in 1Password

Generated here, never typed, never on screen:

```bash
env -u OP_SERVICE_ACCOUNT_TOKEN op item create --vault kdk-ops --category password --title b2-photos-crypt \
  --generate-password=64,letters,digits "salt[password]=$(openssl rand -hex 32)" \
  "notesPlain=rclone crypt password (password) + salt (password2) of the B2 photo copy. Losing it = losing the B2 copy. Not the Proton key. docs/runbooks/photo-offsite.md#b2" >/dev/null
R op://kdk-ops/b2-photos-crypt/password | tr -d '\n' | wc -c     # 64
R op://kdk-ops/b2-photos-crypt/salt | tr -d '\n' | wc -c         # 64
```

#### B4. Host file on kdk-dkr-04

```bash
ssh kdkadmin@10.1.20.22 'sudo install -d -m 700 -o root -g root /etc/kdk/backup-photos-b2 && sudo chmod 700 /etc/kdk/backup-photos-b2'
printf 'PHOTOS_B2_ACCOUNT=%s\nPHOTOS_B2_KEY=%s\nPHOTOS_B2_BUCKET=%s\nPHOTOS_B2_CRYPT_PASSWORD=%s\nPHOTOS_B2_CRYPT_SALT=%s\n' \
  "$(R op://kdk-ops/b2-photos-app-key/username)" "$(R op://kdk-ops/b2-photos-app-key/credential)" \
  "$(R op://kdk-ops/b2-photos-app-key/bucket)" "$(R op://kdk-ops/b2-photos-crypt/password)" "$(R op://kdk-ops/b2-photos-crypt/salt)" \
  | ssh kdkadmin@10.1.20.22 'sudo install -m 600 -o root -g root /dev/stdin /etc/kdk/backup-photos-b2/b2.env'
ssh kdkadmin@10.1.20.22 'sudo stat -c "%a %U %n" /etc/kdk/backup-photos-b2 /etc/kdk/backup-photos-b2/b2.env; sudo awk -F= "{ print \$1, length(\$0) - length(\$1) - 1 }" /etc/kdk/backup-photos-b2/b2.env'
```

Expect `700 root`, `600 root`, then the five names with lengths only: `PHOTOS_B2_ACCOUNT 25`,
`PHOTOS_B2_KEY 31`, `PHOTOS_B2_BUCKET 19`, `PHOTOS_B2_CRYPT_PASSWORD 64`,
`PHOTOS_B2_CRYPT_SALT 64` (key lengths are Backblaze's current format; any length > 0 for
the first two is fine).

#### B5. Prove the key can hide but not erase (laptop)

From a checkout of this branch on the laptop (`rclone` ≥ 1.75):

```bash
B=stacks/backup-photos/b2-rclone.sh; BK=$(R op://kdk-ops/b2-photos-app-key/bucket)
sh $B --op lsf "b2photos:$BK"                                   # empty, exit 0
echo key-test | sh $B --op rcat "b2photos:$BK/key-test.txt"     # upload works
sh $B --op deletefile "b2photos:$BK/key-test.txt"                # a sync-style delete = hide: exit 0
sh $B --op --b2-versions lsf "b2photos:$BK"                      # key-test-v2026-...txt: the hidden version is kept
V=$(sh $B --op --b2-versions lsf "b2photos:$BK" | grep '^key-test-v'); sh $B --op --b2-versions deletefile "b2photos:$BK/$V"
                                                                # must FAIL: 401 unauthorized (no deleteFiles)
b2api b2_list_buckets "$(jq -nc --arg a "$ACCT" --arg b "$BUCKET" '{accountId:$a, bucketName:$b}')" | jq '.buckets[0] | {bucketType, lifecycleRules}'
```

The test version expires on its own 90 days after the hide (the lifecycle rule). If the
versions `delete` **succeeds**, the key has `deleteFiles`: stop, delete that key
(`b2api b2_delete_key '{"applicationKeyId":"<id>"}'`) and redo B2. Then
`unset AUTH API ACCT BUCKET BID`. Only now merge.

### After merge (owner)

The gitops-sync ResourceSync (≤ 15 min) is all Komodo needs: `backup-photos` is deployed by
the nightly procedure, no other stack changes.

#### A1. Dry run from a checkout on kdk-dkr-04

During the day. B2 only (`PHOTOS_REMOTES=b2`: Proton and its session are not touched), one
pass, nothing uploaded, no status written:

```bash
ssh kdkadmin@10.1.20.22
git clone --depth 1 -b main https://github.com/BigH3ater/kdk-lab-dkr.git /tmp/kdk-photos && cd /tmp/kdk-photos/stacks/backup-photos
sudo docker compose run --rm --no-deps -e PHOTOS_REMOTES=b2 -e PHOTOS_DRY_RUN=1 -e PHOTOS_B2_MAX_PASSES=1 photos 2>&1 | grep -v 'Skipped copy as --dry-run' | tail -20
```

Expect the two `backup-manifest ... guard=ok` lines, `photos db: remote=b2 rclone exit=0
files=15 ... guard=ok`, `photos rclone exit=0 remote=b2 passes=1 ... guard=ok mode=sync`
and `photos: dry run, /data/.photos-status-b2 not written`. An `unauthorized` /
`401` here means the key or bucket name in `b2.env` is wrong (redo B4); `not configured`
means `b2.env` is missing or a line is empty.

#### A2. First real run (seed), still from the checkout

```bash
sudo docker compose run -d --rm --no-deps --name backup-photos-b2-seed -e PHOTOS_REMOTES=b2 -e PHOTOS_B2_MAX_PASSES=4 photos
sudo docker logs -f backup-photos-b2-seed 2>&1 | grep -E '^backup-manifest|^photos|NOTICE'     # Ctrl-C leaves it running
```

Up to 4 × 60 min; start it at least 5 h before 03:20, or use the default 2 passes and let
the nightly finish the seed. When it has exited:
`sudo cat /mnt/backup/dkr/.photos-status-b2` then `cd / && rm -rf /tmp/kdk-photos`.

- While seeding: `photos pass N: remote=b2 rclone exit=10 secs=36xx`, status
  `rc=10 ... guard=ok last_ok=0 seed_start=<now> crashes=0 db_rc=0 db_guard=ok db_last_ok=<now>`.
- Done: `rc=0 ... last_ok=<time>`.

#### A3. The next mornings

- Pushover: `photos/b2: first upload still seeding (day N, ...)` WARN until B2 is complete,
  then `kdk-lab backup OK` ("photo originals in sync on Proton and B2, Immich DB on B2").
- Grafana: **Photos on B2** `in sync`, **Immich DB on B2** `in sync`, Files table B2 column
  `on B2`. The first night after `rc=0`, the B2 pass takes minutes (only the day's new
  photos); if every night re-uploads everything, report it (modtimes are changing).

#### A4. Restore test B2 (gate for the second phone)

From the **laptop** (the case that matters: the lab is gone), with only 1Password and
rclone, after a night with B2 `rc=0`. Pick the files on dkr-04 as in
[step 7](#7-restore-test-gate-for-the-second-phone):

```bash
B=stacks/backup-photos/b2-rclone.sh
P=$(ssh kdkadmin@10.1.20.22 'sudo find /mnt/immich/library -type f ! -name .immich -mtime +2 | shuf -n 1'); echo "$P"
I=$(ssh kdkadmin@10.1.20.22 'sudo find /mnt/insta360-flat -type f -mtime +2 | shuf -n 1'); echo "$I"
sh $B --op lsf b2photoscrypt:                                                  # immich-db/  photos/
sh $B --op cat "b2photoscrypt:photos/immich/${P#/mnt/immich/}" | sha256sum
sh $B --op cat "b2photoscrypt:photos/insta360-flat/${I#/mnt/insta360-flat/}" | sha256sum
ssh kdkadmin@10.1.20.22 "sudo sha256sum '$P' '$I'"                             # the same two hashes
D=$(sh $B --op lsf b2photoscrypt:immich-db/ | sort | tail -1); echo "$D"       # yesterday's or today's dump
sh $B --op cat "b2photoscrypt:immich-db/$D" | gzip -t && echo "db dump OK"
sh $B --op copy "b2photoscrypt:photos/immich/${P#/mnt/immich/}" /tmp/b2-restore/ && xdg-open "/tmp/b2-restore/${P##*/}"
rm -r /tmp/b2-restore
```

The same from **kdk-dkr-04** (reads `b2.env`; in the checkout of A1, with `P` picked there
by the same `find`):

```bash
sudo docker run --rm -v /etc/kdk/backup-photos-b2:/b2:ro -v "$PWD/b2-rclone.sh:/b2-rclone.sh:ro" --entrypoint /bin/sh \
  rclone/rclone:1.76.0 /b2-rclone.sh cat "b2photoscrypt:photos/immich/${P#/mnt/immich/}" | sha256sum
```

Record the date, both paths, "hashes match" and "db dump OK" in the vault
(`10.Homelab/TODO.README.md`, Immich section), next to the Proton restore test. Both tests
pass = enrol the second phone.

**Real restore from B2**: as [Restore (real)](#restore-real), with
`sh b2-rclone.sh [--op] copy b2photoscrypt:photos/<path>/ <scratch dir>` instead of the
Proton container, and `b2photoscrypt:immich-db/immich-<date>.sql.gz` for the DB. A file
deleted or replaced within the last 90 days: list it with `--b2-versions` (it appears as
`<name>-vYYYY-MM-DD-HHMMSS-000.<ext>`) or view the bucket at a time with
`--b2-version-at "2026-12-01 00:00:00"`, then `copy` it out. Never `sync` from B2 back over
the live library.

### Key rotation (the application key)

The crypt password and salt are **not** rotated in place (that would mean re-encrypting the
whole copy); if they leak, make a new bucket + new crypt item, seed it, and delete the old
bucket after 90 days. The application key, any time (laptop, B1's `R`/`AUTH`/`b2api` set up
again):

```bash
OLD=$(R op://kdk-ops/b2-photos-app-key/username); BID=$(R op://kdk-ops/b2-photos-app-key/bucket_id)
KEY=$(b2api b2_create_key "$(jq -nc --arg a "$ACCT" --arg b "$BID" '{accountId:$a, keyName:"kdk-dkr-04-backup-photos",
  bucketIds:[$b], capabilities:["listBuckets","listFiles","readFiles","writeFiles"]}')")
env -u OP_SERVICE_ACCOUNT_TOKEN op item edit b2-photos-app-key --vault kdk-ops \
  username="$(jq -r .applicationKeyId <<<"$KEY")" "credential[password]=$(jq -r .applicationKey <<<"$KEY")" >/dev/null
unset KEY
```

Then B4 again (rewrite `b2.env`, check the lengths), `sh stacks/backup-photos/b2-rclone.sh
--op lsf b2photoscrypt:` on the laptop (works with the new key), and only then delete the old
one: `b2api b2_delete_key "$(jq -nc --arg k "$OLD" '{applicationKeyId:$k}')" | jq '{keyName, applicationKeyId}'`.
No redeploy: photos.sh reads `b2.env` at every run.

### Rollback (B2 only)

- **Stop B2, keep Proton:** revert this PR (photos.sh, verify, dashboard back to Proton
  only), ResourceSync. Then on kdk-dkr-04
  `sudo rm -r /etc/kdk/backup-photos-b2 && sudo rm /mnt/backup/dkr/.photos-status-b2`.
  (Setting `PHOTOS_REMOTES=proton` alone is not enough: verify would page `photos/b2:
  status stale` every night.)
- **The bucket stays** with its data and 90-day history; deleting it (or the key, or the
  1Password items) is an owner decision, done with the master key, never scripted.
