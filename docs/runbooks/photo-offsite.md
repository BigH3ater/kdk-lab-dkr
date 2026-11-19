---
title: Photo originals offsite (Immich + Insta360 → Proton)
updated: 2026-11-19
---

# Photo originals offsite

The phone originals Immich stores on the NAS and the finished Insta360 stills go to Proton
Drive every night, **file by file** through the existing rclone crypt remote, as their own
stage (`photos`) of the `nightly-backups` procedure. Each photo is uploaded once; a normal
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
            writes /mnt/backup/dkr/.photos-status-proton ──> backup-verify (dkr-01) ──> Pushover
            logs backup-manifest kind=photos ──> Loki ──> Grafana "Nightly backups"
```

| Item | Value |
|---|---|
| Stack | `stacks/backup-photos/` (`photos.sh`, `compose.yaml`), Komodo stack `backup-photos` on kdk-dkr-04, `deploy = false` (run by the procedure) |
| Stage | `photos` in `nightly-backups`, between `offsite` and `verify` |
| Sources | `/mnt/immich/{library,upload,profile}` (new ro NFS mount of `tankz3/appdata/immich`, unit `hosts/kdk-dkr-04/systemd/mnt-immich.mount`) and `/mnt/insta360-flat` (dkr-04's rw mount for the [[insta360-flat]] worker, unit `hosts/kdk-dkr-04/systemd/mnt-insta360\x2dflat.mount`, bound read-only here) |
| NAS export | `/mnt/tankz3/appdata/immich`, hosts `172.16.30.52` only, read-only, `mapall` = `nobody`/`nogroup` |
| Destination | `proton-crypt:photos/immich/{library,upload,profile}/...`, `proton-crypt:photos/insta360-flat/<trip>/...` (same crypt key as every other backup: 1Password `kdk-ops` / `proton-mirror-crypt`) |
| Status | `/mnt/backup/dkr/.photos-status-proton` (`rc= ts= date= passes= guard= last_ok= seed_start= crashes=`), guard counts `/mnt/backup/dkr/.photos-state` |
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

**`sync`, not `copy` (owner decision pending).** Immich keeps a deleted photo in its trash
for 30 days, then removes the file; the tankz3 snapshots keep it 2 more weeks; PR 4b's B2
copy keeps 90 days of versions. With `sync`, Proton is a mirror: a deliberate delete leaves
Proton the night the trash purges it. `copy` never deletes, so Proton would keep every
deliberately deleted photo forever, and every file the storage template renames (editing a
photo's date moves it) would exist twice. Until B2 lands, a photo purged from the Immich
trash is recoverable only from NAS snapshots for 2 weeks. If that gap matters, set
`PHOTOS_MODE=copy` in the stack's `environment` in `komodo/resources.toml` until 4b, then
switch back.

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

## Rollback

1. Revert the PR, ResourceSync (removes the `photos` stage and the `backup-photos` stack;
   `immich` dropped from `storage-health-dkr04` `NFS_MOUNTS`), Deploy
   `storage-health-dkr04` **before** unmounting, or it pages MISSING.
2. kdk-dkr-04: `sudo systemctl disable --now mnt-immich.mount && sudo rm /etc/systemd/system/mnt-immich.mount && sudo systemctl daemon-reload`;
   `sudo rm /mnt/backup/dkr/.photos-status-proton /mnt/backup/dkr/.photos-state`.
3. NAS: `sudo midclt call sharing.nfs.delete <id>` (the id from step 1's query).
4. The Proton copy under `photos/` stays; the host offsite sync never touches it (outside its
   `--filter` set). Removing it is an owner decision: do not script a delete.

## B2 (PR 4b)

Same script, second remote: a `b2)` case in `dest_of`/`flags_of` in `photos.sh` (`b2-crypt:photos`,
its own crypt key in 1Password), `PHOTOS_REMOTES="proton b2"`, `for r in proton b2` in
`verify.sh`, and the dashboard queries repeated with `remote=b2`. The guard runs once per
night for both remotes. 90-day versioning comes from the bucket's lifecycle rule
(`daysFromHidingToDeleting=90`): rclone's deletes on B2 only hide files.
