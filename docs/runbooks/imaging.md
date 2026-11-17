---
title: Imaging (Insta360 trips → Immich)
updated: 2026-11-16
---

# Imaging

Raw Insta360 X4 captures go to the NAS, the nightly [[insta360-flat]] job on
kdk-dkr-04 turns each into two flat stills, and [[immich]] shows them through a read-only
external library with one album per trip.

**Related:** [[insta360-flat]] · [[immich]] · [[kdk-nas-01]] · [[kdk-dkr-04]] · [CAD library](cad-library.md) (same NAS pattern)

```text
camera/laptop --SMB (user insta360-upload)--> tankz3/insta360-upload/<Trip>/        snapshots daily 03:00, 2 w
kdk-dkr-04 /mnt/insta360-upload (NFS ro, mapall 3012) --insta360-flat 02:35-->
kdk-dkr-04 /mnt/insta360-flat   (NFS rw, mapall 3003:3001) -> tankz3/insta360-flat/<YYYY-MM>-<trip>/   snapshots daily 03:00, 2 w
kdk-dkr-dmz-01 /mnt/insta360-flat (NFS ro, mapall 3013) -> Immich library "Insta360" -> immich-albums 02:10
```

## Add a trip

1. **Copy the raws** (`.insp` and camera `.jpg`; `.insv`/`.lrv` video may come along, it
   is ignored) into a new top-level folder on `\\10.1.3.5\insta360-upload`, signed in as
   `insta360-upload` (password: 1Password `kdk-ops/kdk-nas-01-insta360-upload-smb`).
   The folder name becomes the trip slug: `Norway 2026` → `2026-06-norway-2026` (YYYY-MM of
   the earliest capture). Linux: `smbclient //10.1.3.5/insta360-upload -U insta360-upload`
   or a file manager; macOS Finder → Connect to Server `smb://10.1.3.5/insta360-upload`.
2. **Optional `places.tsv`** in the same folder, tab-separated, one row per place:
   `date (YYYYMMDD)  start (HHMM)  end (HHMM)  slug`, lines starting `#` ignored, times
   compared on the capture's `HHMM` (camera clock). No file or no matching row → the trip
   slug. Example: [`imaging-maine-places.tsv`](imaging-maine-places.tsv) (Maine 2025-08).
   **Add it before the first run**: stills are never renamed later.
   Raws still being copied are safe: anything changed in the last 15 min is deferred to
   the next run, and a truncated `.insp` fails instead of becoming an unleveled still.
3. **Wait for 02:35**, or run the Komodo Procedure `insta360-flat` now. A trip of ~300
   captures takes ~35 min (3 workers; Maine 326 captures: 34 min on 2026-11-17).
4. **Check**: `ssh kdkadmin@10.1.20.22 'sudo docker logs insta360-flat 2>&1 | tail -1'` →
   `failed=0`. The stills are in `tankz3/insta360-flat/<YYYY-MM>-<trip>/`.
5. **Immich** imports them on its next scan and `immich-albums` makes the album at the next
   02:10 run (the night after; run the `immich-albums` Procedure to see it sooner).

To redo a still (better `places.tsv`, a recipe change): delete its `a`/`b` files on the
flat dataset by hand (NAS, or SMB as an admin); the job only fills gaps. Immich treats the
re-made file as a new asset (old one → trash).

## Known limits

- **No GPS** in the X4 files: places are manual (`places.tsv`). `exif_gps.py` (laptop)
  prints what EXIF there is.
- **Seam** when the camera pointed straight up or down: leveling falls back to the camera's
  top (`lens nearly straight up/down`), and the stitch line of the dual fisheye can show.
- **Bursts collapsed**: 3 or more captures in the same second keep only the lowest sequence
  number (Maine: 019–033 → 019, 034–049 → 034); a same-second pair is two shots (Maine 324 +
  325, both kept).
- **Video ignored** (`.insv`, `.lrv`, `.mp4`); stills only.
- **PhotoCraft is early alpha**, pinned to exactly v0.23.1 by sha256; CPU only (`--no-gpu`).
- Stills already on the share are never redone; output bytes differ slightly between
  builds/hosts (laptop vs. dkr-04: same names and sizes, PSNR 45–65 dB).

## Laptop tools (`~/cad/tools/imaging`)

The proven originals stay on the laptop; the job is a port of them. Still useful by hand
(not in the repo):

| Script | Use |
|---|---|
| `contact-sheet-360.sh <capture> <out-dir>` | four flat views (yaw 0/90/180/−90) in a 2×2 grid, to pick headings or name places |
| `exif_gps.py <files>` | EXIF time and GPS (TSV), stdlib only |
| `process-one.sh`, `level.py`, `insp_trailer.py`, `flat-360.sh`, `touch.sh`, `slug.py` | the reference pipeline `flatten.py` ports |
| `run-batch.maine.sh` | the one-off Maine laptop batch (326 captures → 652 stills, 0 failures) |

## NAS objects (created 2026-11-16)

| Object | Value |
|---|---|
| Datasets | `tankz3/insta360-upload` (raws, owner 3012:3012), `tankz3/insta360-flat` (stills, owner 3003:3001), NFSv4 ACL, LZ4; the original `tankz3/insta360` (SMB share id 3) is the copy source, not used by the job |
| Users | `insta360-upload` uid/gid **3012** (SMB, nologin, `/var/empty`; password in 1P `kdk-ops/kdk-nas-01-insta360-upload-smb`), `insta360-ro` uid/gid **3013** (no SMB, password disabled; the Immich host's NFS identity) |
| ACL upload | `owner@` FULL, `group@` MODIFY, **`USER 3012` MODIFY** (all INHERIT; applied 2026-11-16, below) |
| ACL flat | `owner@` FULL, `group@` MODIFY, `USER 3013` READ (all INHERIT) |
| SMB | share id **8** `insta360-upload` → `/mnt/tankz3/insta360-upload` (DEFAULT_SHARE) |
| NFS | **236** flat → `172.16.30.51` (dmz-01) ro, mapall `insta360-ro`; **237** upload → `172.16.30.52` (dkr-04) ro, mapall `insta360-upload`; **238** flat → `172.16.30.52` rw, mapall `jmack`/`jmack` |
| Snapshots | tasks **17** (upload), **18** (flat), **19** (`tankz3/insta360`): daily 03:00, kept 2 WEEK, `auto-%Y-%m-%d_%H-%M`, recursive |
| dkr-04 units | `mnt-insta360\x2dupload.mount` (`ro`), `mnt-insta360\x2dflat.mount` (rw), `hosts/kdk-dkr-04/systemd/`, cloud-init copy |
| dmz-01 unit | `mnt-insta360\x2dflat.mount` (`ro`), `hosts/kdk-dkr-dmz-01/systemd/` |

The midclt calls, reconstructed from the objects above (NAS, `ssh kdkadmin@10.1.3.5`; the
SMB password goes in over stdin as in [CAD library](cad-library.md) step 1c):

```sh
sudo midclt call pool.dataset.create '{"name":"tankz3/insta360-upload","acltype":"NFSV4","aclmode":"RESTRICTED"}'
sudo midclt call pool.dataset.create '{"name":"tankz3/insta360-flat","acltype":"NFSV4","aclmode":"RESTRICTED"}'
# owners + ACLs: upload 3012:3012 owner@/group@ (+ USER 3012, see "Upload ACL" below); flat 3003:3001 owner@/group@ + USER 3013 READ
sudo midclt call group.create '{"name":"insta360-upload","gid":3012,"smb":true}'
sudo midclt call group.create '{"name":"insta360-ro","gid":3013,"smb":false}'
# user insta360-upload (uid 3012, smb true, password from 1P over stdin), then:
sudo midclt call user.create '{"username":"insta360-ro","full_name":"insta360 read-only (Immich NFS)","uid":3013,"group_create":false,"group":<gid 3013 id>,"home":"/var/empty","shell":"/usr/sbin/nologin","smb":false,"password_disabled":true}'
sudo midclt call sharing.smb.create '{"path":"/mnt/tankz3/insta360-upload","name":"insta360-upload","purpose":"DEFAULT_SHARE","comment":"Insta360 raw trip uploads, insta360-upload login only (docs/runbooks/imaging.md)"}'
sudo midclt call sharing.nfs.create '{"path":"/mnt/tankz3/insta360-flat","hosts":["172.16.30.51"],"ro":true,"mapall_user":"insta360-ro","mapall_group":"insta360-ro","comment":"Finished Insta360 stills, read-only to kdk-dkr-dmz-01 for Immich (docs/runbooks/imaging.md)"}'
sudo midclt call sharing.nfs.create '{"path":"/mnt/tankz3/insta360-upload","hosts":["172.16.30.52"],"ro":true,"mapall_user":"insta360-upload","mapall_group":"insta360-upload","comment":"Insta360 raw uploads, read-only to kdk-dkr-04 for the insta360-flat worker (docs/runbooks/imaging.md)"}'
sudo midclt call sharing.nfs.create '{"path":"/mnt/tankz3/insta360-flat","hosts":["172.16.30.52"],"ro":false,"mapall_user":"jmack","mapall_group":"jmack","comment":"Finished Insta360 stills, read-write to kdk-dkr-04 for the insta360-flat worker (docs/runbooks/imaging.md)"}'
for ds in tankz3/insta360-upload tankz3/insta360-flat tankz3/insta360; do
  sudo midclt call pool.snapshottask.create "{\"dataset\":\"$ds\",\"recursive\":true,\"lifetime_value\":2,\"lifetime_unit\":\"WEEK\",\"naming_schema\":\"auto-%Y-%m-%d_%H-%M\",\"allow_empty\":true,\"enabled\":true,\"schedule\":{\"minute\":\"0\",\"hour\":\"3\",\"dom\":\"*\",\"month\":\"*\",\"dow\":\"*\",\"begin\":\"00:00\",\"end\":\"23:59\"}}"
done
```

Verify (NAS):

```sh
sudo midclt call sharing.nfs.query '[["id","in",[236,237,238]]]' | jq -c '.[] | {id,path,hosts,ro,mapall_user}'
sudo midclt call sharing.smb.query '[["id","=",8]]' | jq -c '.[0] | {name,path,enabled}'
sudo midclt call pool.snapshottask.query '[["id","in",[17,18,19]]]' | jq -c '.[] | {id,dataset,lifetime_value,lifetime_unit,hour:.schedule.hour}'
sudo midclt call filesystem.getacl /mnt/tankz3/insta360-upload | jq -c '[.acl[] | [.tag,.id,.perms.BASIC]]'   # includes ["USER",3012,"MODIFY"]
```

### Upload ACL: `USER 3012` (done 2026-11-16)

The upload export squashes every dkr-04 identity to uid/gid 3012 **with no supplementary
groups**, and OpenZFS matches `group@` only against supplementary groups, so `group@`
never matches over this export. Files copied in as root (the Maine raws: `0:3012`) are
then unreadable (`Permission denied` on `/mnt/insta360-upload/Maine`, seen 2026-11-16).
Same fix as `USER 3011` on `3d-prints`: an explicit entry, applied recursively.

```sh
D=/mnt/tankz3/insta360-upload
ACL=$(sudo midclt call filesystem.getacl $D | jq -c '[.acl[] | select(.tag != "USER" or .id != 3012) | {tag,id,type,perms,flags}] + [{tag:"USER",id:3012,type:"ALLOW",perms:{BASIC:"MODIFY"},flags:{BASIC:"INHERIT"}}]')
echo "$ACL" | jq -c '.[] | [.tag,.id,.perms.BASIC]'    # owner@ FULL, group@ MODIFY, USER 3012 MODIFY
sudo midclt call -j filesystem.setacl "$(jq -nc --arg p $D --argjson a "$ACL" '{path:$p,dacl:$a,acltype:"NFS4",options:{recursive:true,traverse:false,canonicalize:true}}')"
```

Then from dkr-04 (allow ~1 min for the client's attribute cache):
`ssh kdkadmin@10.1.20.22 'ls /mnt/insta360-upload/Maine | wc -l'` → `460` (with `places.tsv`).

Applied on 2026-11-16 (owner-approved NAS lane) with the commands above; verified from
dkr-04: the listing works, a raw's md5 matches the laptop copy, writes still fail with
`Read-only file system`. The commands stay here as the rebuild record.

### Maine `places.tsv` (done 2026-11-16)

`insta360-upload/Maine/places.tsv` = [`imaging-maine-places.tsv`](imaging-maine-places.tsv)
(owner 3012:3012, md5 `530c1e599fe1c4f8021b1f1609b417c6`), installed on the NAS with:

```sh
ssh kdkadmin@10.1.3.5 'sudo install -o 3012 -g 3012 -m 0660 /dev/stdin /mnt/tankz3/insta360-upload/Maine/places.tsv' < docs/runbooks/imaging-maine-places.tsv
```

## kdk-dkr-04 mounts

```sh
# from the repo checkout on the laptop
scp 'hosts/kdk-dkr-04/systemd/mnt-insta360\x2dupload.mount' 'hosts/kdk-dkr-04/systemd/mnt-insta360\x2dflat.mount' kdkadmin@10.1.20.22:/tmp/
ssh kdkadmin@10.1.20.22 'for u in "mnt-insta360\x2dupload.mount" "mnt-insta360\x2dflat.mount"; do sudo install -m 644 "/tmp/$u" "/etc/systemd/system/$u" && rm "/tmp/$u"; done; sudo systemctl daemon-reload; sudo systemctl enable --now "mnt-insta360\x2dupload.mount" "mnt-insta360\x2dflat.mount"'
ssh kdkadmin@10.1.20.22 'findmnt -rn -o TARGET,OPTIONS /mnt/insta360-upload /mnt/insta360-flat | cut -c1-40; touch /mnt/insta360-upload/x'   # ro / rw; touch: Read-only file system
```

Installed on kdk-dkr-04 2026-11-16 (both `active`; a test file on flat landed as
`3003:3001` on the NAS and was removed).

## Validate a change (image or `flatten.py`)

Build on dkr-04 from a temp dir (never `/etc/komodo`, never the Komodo clone), then run the
compose file with an override that points `/flat` at a scratch dir and feeds a few real raws:

```sh
T=$(mktemp -d /var/tmp/i360-test.XXXXXX)      # upload-a/<Trip>/ with a few real raws, flat-scratch/ (chown 3003:3001)
cd <temp copy of stacks/insta360-flat> && sudo docker build -t kdk/insta360-flat:test .
sudo docker compose -p i360test -f compose.yaml -f test.yaml run --rm --pull never flatten
# test.yaml: image kdk/insta360-flat:test; volumes $T/upload-a:/upload:ro, $T/flat-scratch:/flat,
#            an empty file at /pushover.env, optional $T/places:/places:ro + PLACES_DIR=/places
```

Compare names, `ffprobe` dimensions and PSNR against the existing stills; then one run
against the real `/flat` must report `made=0` for a trip that is already done. Remove `$T`,
the temp dir, the `:test` image and the `i360test_default` network afterwards.

## Rollback

- Stop the nightly run: Komodo → Procedure `insta360-flat` → disable the schedule (or revert
  the PR and ResourceSync).
- Stills already written stay (the job never deletes). Remove a bad batch on the NAS:
  `sudo rm /mnt/tankz3/insta360-flat/<trip>/<pattern>` (snapshots keep 2 weeks).
- Units: `sudo systemctl disable --now 'mnt-insta360\x2dupload.mount' 'mnt-insta360\x2dflat.mount'`
  and delete them from `/etc/systemd/system/` on dkr-04.
