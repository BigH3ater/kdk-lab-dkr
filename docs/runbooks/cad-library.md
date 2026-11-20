---
title: CAD library (laptop → NAS → offsite)
updated: 2026-11-20
---

# CAD library

The owner's CAD work lives in `~/cad` on the laptop **mbp-dev** (SolidCraft `.sc`
sources and `.sc.bak`, STEP/STL/3MF exports, sliced gcode, reference photos, slicer
presets, small tools). Every 15 minutes a user timer pushes it to the existing SMB share
`3d-prints` on kdk-nas-01; the NAS snapshots it, kdk-dkr-04 mounts it read-only, and the
nightly `backup-offsite` stage packs it into `offsite/3d-prints-YYYY-MM-DD.tar.zst`,
which goes to Proton with the per-host archives.

**Related:** [[kdk-nas-01]] · [[kdk-dkr-04]] · [[backups]] · [linux-tools share](linux-tools-share.md) (same NAS/dkr-04 pattern) · `hosts/mbp-dev/README.md`

```text
laptop ~/cad --cad-sync (rsync -rt, 15 min, no --delete)--> SMB //10.1.3.5/3d-prints  (user cad-sync)
                                                             | tankz3/3d-prints: snapshots hourly 2 d, daily 2 w
                                       NFS ro, mapall=cad-sync|
kdk-dkr-04 /mnt/3d-prints --pack (nightly)--> /mnt/backup/dkr/offsite/3d-prints-DATE.tar.zst (keep 3) --rclone--> Proton (crypt)
                         backup-verify (dkr-01): archive present + zstd -t (fail); laptop .last-sync < 14 d (warn)
```

| Item | Value |
|---|---|
| Dataset | `tankz3/3d-prints`, mountpoint `/mnt/tankz3/3d-prints`, NFSv4 ACL, owner `jmack` 3003, group 3001 (`jmack`) |
| SMB | share `3d-prints` (purpose DEFAULT_SHARE, bound to `10.1.3.5`/`10.1.1.5`/`10.1.20.5`, SMB1 off) |
| Sync identity | NAS user + group `cad-sync`, uid/gid **3011**, SMB only, shell `nologin`, home `/var/empty`; password in 1Password `kdk-ops/kdk-nas-01-cad-sync-smb` (Login) |
| ACL | existing `owner@` FULL, `group@` MODIFY, `USER 3003` FULL, plus `USER 3011` MODIFY, all `INHERIT` (applied recursively) |
| Snapshots | hourly kept 2 DAY + daily 03:00 kept 2 WEEK, `auto-%Y-%m-%d_%H-%M` (same as `tankz3/appdata`) |
| NFS export | `/mnt/tankz3/3d-prints`, `hosts = ["172.16.30.52"]` only, **read-only**, `mapall_user/group = cad-sync` |
| dkr-04 mount | `/mnt/3d-prints`, unit `mnt-3d\x2dprints.mount`, `nfsvers=4.1,ro,noatime,_netdev` (`hosts/kdk-dkr-04/systemd/`) |
| Laptop mount | `/mnt/3d-prints`, `mnt-3d\x2dprints.automount` + `.mount` (cifs `vers=3.1.1,seal`), creds `/etc/cifs/3d-prints.cred` root 0600 (`hosts/mbp-dev/systemd/`) |
| Laptop sync | `~/.local/bin/cad-sync.sh` + user `cad-sync.service`/`.timer` (`hosts/mbp-dev/`) |
| Offsite | `backup-offsite` `pack` → `offsite/3d-prints-YYYY-MM-DD.tar.zst`, newest 3 kept, excludes `*.log`, `__pycache__`, `.~lock*`, `.~*.lock` |
| Checks | `storage-health-dkr04` `NFS_MOUNTS` includes `3d-prints` (full list in `komodo/resources.toml`); `backup-verify` (see Verify) |

### Why mapall to `cad-sync` on the NFS export

The dataset is mode `0770`-equivalent with no `everyone@` entry. The `pack` container
reads as root (with `cap_drop: ALL`); without a mapping, NFS squashes root to `nobody`
and every file is denied. On NFS the server's ACL decides access (the client asks it
with ACCESS calls, which is why `pack` already reads the 0700 host trees on the
`maproot=root` backup export without `CAP_DAC_*`). Files on the share are owned either by
`jmack` 3003 (the September `desk-cable-management/` files) or by `cad-sync` 3011
(everything the laptop writes over SMB: Samba creates files as the logged-in user), and
both carry the inherited `USER 3011 MODIFY` entry once step 1 applies it recursively.
So mapping every dkr-04 identity to `cad-sync` reads every file, and the export being
`ro` means that identity can never write through NFS. `maproot` alone would leave
kdkadmin (uid 1000 = the NAS's `tdarr-smb`) with a meaningless identity; mapall to
`jmack` would work but hands dkr-04 the owner's identity for no gain. TrueNAS refuses
`maproot` together with `mapall`, and mapall covers root.

### What the sync does and does not do

- **One-way, additive.** `rsync -rt --modify-window=2`, no `--delete`: a file deleted or
  renamed on the laptop stays on the NAS. Clean up on the share by hand (SMB) if it
  matters. CIFS has no unix owners/modes, so no `-a`/`-o`/`-g`/`-p`.
- **New project folders just work** (`-r`); nothing lists projects anywhere.
- **Excluded:** `*.log`, `__pycache__/`, lock files (LibreOffice `.~lock*`, SolidCraft
  `.~<name>.sc.lock` = `.~*.lock`), the share's own `.last-sync`, and any name with a
  backslash (SMB cannot store it; one such file failed every run from 2026-11-16 to
  2026-11-20 with `rsync failed rc=23` and froze `.last-sync`; cad-sync now names them in
  its log and skips them). A lock file pushed
  before the `.~*.lock` exclude stays on the share (no `--delete`); `pack` skips it, delete
  it over SMB if it bothers you (e.g. `relay-bracket/.~test-cube20.sc.lock`, seen 2026-11-16).
  `.sc.bak`, gcode, renders and jpg references all go.
- **Away from home** it exits 0 silently: a 3 s TCP probe to `10.1.3.5:445`, then a
  30 s-guarded `stat -f` that must say cifs/smb2 (so it never writes into the bare local
  `/mnt/3d-prints` directory). rsync exit 24 (a file vanished mid-save) counts as success.
- **Marker:** on success it writes `/mnt/3d-prints/.last-sync` (`<ISO time> <hostname>`).
  The timer is a **user** timer: it runs while jmack is logged in, which is when `~/cad`
  changes.

## Order

1. NAS: 1Password item, `cad-sync` user, ACL, snapshot tasks, NFS export.
2. kdk-dkr-04: install + enable the mount unit, verify.
3. Laptop: packages, credentials, automount, pull back NAS-only files, then the timer.
4. Merge the lab PR, ResourceSync, Deploy `storage-health-dkr04`.
5. First night: check the archive and the `backup-verify` result.

**Never merge (step 4) before step 2 verifies:** storage-health would page
`/mnt/3d-prints MISSING` every hour, and `pack` would skip the archive so
`backup-verify` fails the night. Never enable the timer (3f) before the pull-back (3e).

## 1. NAS (owner)

**1a. Generate the password in 1Password (laptop, `op` signed in).** Letters and digits
only, so it is safe in the cifs credentials file:

```sh
op item create --vault kdk-ops --category login --title kdk-nas-01-cad-sync-smb \
  --generate-password=letters,digits,32 username=cad-sync \
  'notesPlain=SMB user for the laptop CAD sync to //10.1.3.5/3d-prints (docs/runbooks/cad-library.md)' >/dev/null
op read 'op://kdk-ops/kdk-nas-01-cad-sync-smb/password' | wc -c     # 33 (32 + newline)
```

**1b. Group (NAS, `ssh kdkadmin@10.1.3.5`)**

```sh
sudo midclt call user.query '[["uid","=",3011]]'; sudo midclt call group.query '[["gid","=",3011]]'   # both []
sudo midclt call group.create '{"name":"cad-sync","gid":3011,"smb":false}'
```

**1c. User, password from 1Password over ssh stdin (laptop).** Same pattern as the ACME
token in [[kdk-nas-01]]: the password never appears on a command line:

```sh
op read 'op://kdk-ops/kdk-nas-01-cad-sync-smb/password' | ssh kdkadmin@10.1.3.5 'sudo python3 -c "
import sys
from truenas_api_client import Client
pw=sys.stdin.read().strip()
with Client() as c:
    g=c.call(\"group.query\",[[\"gid\",\"=\",3011]])[0][\"id\"]
    c.call(\"user.create\",{\"username\":\"cad-sync\",\"full_name\":\"cad-sync SMB (laptop CAD library)\",\"uid\":3011,\"group\":g,\"group_create\":False,\"home\":\"/var/empty\",\"shell\":\"/usr/sbin/nologin\",\"smb\":True,\"password\":pw})
    print(\"created\")
"'
```

**1d. ACL, snapshots, export (NAS, `ssh kdkadmin@10.1.3.5`).** The ACL keeps every
existing entry and adds `USER 3011 MODIFY INHERIT`; `recursive` stamps it onto the
existing `desk-cable-management/` files too (re-running it is safe: any old 3011 entry
is dropped first):

```sh
D=/mnt/tankz3/3d-prints
ACL=$(sudo midclt call filesystem.getacl $D | jq -c '[.acl[] | select(.tag != "USER" or .id != 3011) | {tag,id,type,perms,flags}] + [{tag:"USER",id:3011,type:"ALLOW",perms:{BASIC:"MODIFY"},flags:{BASIC:"INHERIT"}}]')
echo "$ACL" | jq -c '.[] | [.tag,.id,.perms.BASIC]'    # owner@ FULL, group@ MODIFY, USER 3003 FULL, USER 3011 MODIFY
sudo midclt call -j filesystem.setacl "$(jq -nc --arg p $D --argjson a "$ACL" '{path:$p,dacl:$a,acltype:"NFS4",options:{recursive:true,traverse:false,canonicalize:true}}')"

sudo midclt call pool.snapshottask.create '{"dataset":"tankz3/3d-prints","recursive":true,"lifetime_value":2,"lifetime_unit":"DAY","naming_schema":"auto-%Y-%m-%d_%H-%M","allow_empty":true,"enabled":true,"schedule":{"minute":"0","hour":"*","dom":"*","month":"*","dow":"*","begin":"00:00","end":"23:59"}}'
sudo midclt call pool.snapshottask.create '{"dataset":"tankz3/3d-prints","recursive":true,"lifetime_value":2,"lifetime_unit":"WEEK","naming_schema":"auto-%Y-%m-%d_%H-%M","allow_empty":true,"enabled":true,"schedule":{"minute":"0","hour":"3","dom":"*","month":"*","dow":"*","begin":"00:00","end":"23:59"}}'

sudo midclt call sharing.nfs.create '{"path":"/mnt/tankz3/3d-prints","comment":"CAD library, read-only to kdk-dkr-04 for backup-offsite (docs/runbooks/cad-library.md)","hosts":["172.16.30.52"],"ro":true,"mapall_user":"cad-sync","mapall_group":"cad-sync"}'
```

**1e. Verify (NAS)**

```sh
sudo midclt call user.query '[["username","=","cad-sync"]]' | jq -c '.[0] | {uid,smb,shell,home,locked}'   # 3011, true, nologin, /var/empty, false
sudo midclt call filesystem.getacl /mnt/tankz3/3d-prints/desk-cable-management/README.md | jq -c '[.acl[] | [.tag,.id,.perms.BASIC]]'   # includes ["USER",3011,"MODIFY"]
sudo midclt call pool.snapshottask.query '[["dataset","=","tankz3/3d-prints"]]' | jq -c '.[] | {id,lifetime_value,lifetime_unit,hour:.schedule.hour}'   # 2 DAY "*", 2 WEEK "3"
sudo midclt call sharing.nfs.query '[["path","=","/mnt/tankz3/3d-prints"]]' | jq -c '.[0] | {id,hosts,ro,mapall_user}'
sudo showmount -e localhost | grep 3d-prints      # /mnt/tankz3/3d-prints 172.16.30.52
```

Run `showmount` on the NAS: from dkr-04 it fails by design (the storage fence allows
only 111/2049 to the NAS).

## 2. kdk-dkr-04 mount (owner)

From a repo checkout on the laptop:

```sh
scp 'hosts/kdk-dkr-04/systemd/mnt-3d\x2dprints.mount' kdkadmin@10.1.20.22:/tmp/
ssh kdkadmin@10.1.20.22
sudo install -m 644 '/tmp/mnt-3d\x2dprints.mount' /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now 'mnt-3d\x2dprints.mount' && findmnt /mnt/3d-prints
rm '/tmp/mnt-3d\x2dprints.mount'
ls /mnt/3d-prints                                                      # desk-cable-management
head -c 64 /mnt/3d-prints/desk-cable-management/README.md >/dev/null && echo readable
touch /mnt/3d-prints/.ro-test                                          # must fail: Read-only file system
```

The unit has no `nofail` (same as the other dkr-04 mounts): enable it only after step
1's export exists. A rebuild enables it from cloud-init on its own `runcmd` line
(`|| true`).

## 3. Laptop (owner, on mbp-dev)

On a rebuilt laptop `hosts/mbp-dev/install.sh` (sections `a` and `e`) does 3a–3d and
enables the timer; only 3e/3f stay by hand.

**3a. Packages.** Both are missing on the laptop today (`rsync` too):

```sh
sudo apt install cifs-utils rsync
```

**3b. Credentials file, read from 1Password in the same step** (no secret on any command
line; the file is root 0600):

```sh
sudo install -d -m 700 /etc/cifs
{ echo username=cad-sync; op read 'op://kdk-ops/kdk-nas-01-cad-sync-smb/password' | sed 's/^/password=/'; } \
  | sudo install -m 600 -o root -g root /dev/stdin /etc/cifs/3d-prints.cred
sudo stat -c '%a %U %s' /etc/cifs/3d-prints.cred       # 600 root 60 (18 + 42 bytes)
```

**3c. Mount + automount** (from the repo checkout):

```sh
sudo install -m 644 'hosts/mbp-dev/systemd/mnt-3d\x2dprints.mount' 'hosts/mbp-dev/systemd/mnt-3d\x2dprints.automount' /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now 'mnt-3d\x2dprints.automount'
ls /mnt/3d-prints && findmnt /mnt/3d-prints     # desk-cable-management; cifs //10.1.3.5/3d-prints ... vers=3.1.1,seal
touch /mnt/3d-prints/.rw-test && rm /mnt/3d-prints/.rw-test && echo writable
```

**3d. Install the script and user units, do NOT enable the timer yet:**

```sh
install -D -m 755 hosts/mbp-dev/cad-sync.sh ~/.local/bin/cad-sync.sh
install -D -m 644 hosts/mbp-dev/systemd-user/cad-sync.service hosts/mbp-dev/systemd-user/cad-sync.timer -t ~/.config/systemd/user/
systemctl --user daemon-reload
```

**3e. One-time pull-back** of the files that exist only on the NAS (the September
`desk-cable-management/` set: `render-homelab.sh`, `stl-homelab/`, `_lite-DO-NOT-USE/`
and so on). `--ignore-existing` never overwrites a laptop file:

```sh
rsync -rtn --ignore-existing -i /mnt/3d-prints/ ~/cad/    # preview
rsync -rt --ignore-existing /mnt/3d-prints/ ~/cad/
```

**3f. First push by hand, then the timer:**

```sh
~/.local/bin/cad-sync.sh --dry-run | tail -20     # what would go up
systemctl --user start cad-sync.service && journalctl --user -u cad-sync -n 20 --no-pager
cat /mnt/3d-prints/.last-sync                       # <ISO time> mbp-dev
systemctl --user enable --now cad-sync.timer && systemctl --user list-timers cad-sync.timer
```

## 4. Merge + storage-health

Merge the PR. After the gitops-sync ResourceSync (≤15 min, or run it): Komodo UI,
Stacks, `storage-health-dkr04`, Deploy (or wait for the hourly `storage-health`
procedure). Log line: `storage-health OK (172.16.30.2, mounts: media backup linux-tools 3d-prints)`.
`backup-offsite` and `backup-verify` are deployed from the repo by the nightly
procedure, so nothing else to deploy.

## 5. First night

The morning after the merge:

```sh
ssh kdkadmin@10.1.20.22 'sudo ls -la /mnt/backup/dkr/offsite/ | grep 3d-prints'     # 3d-prints-YYYY-MM-DD.tar.zst
ssh kdkadmin@10.1.20.22 'sudo zstd -t -q /mnt/backup/dkr/offsite/3d-prints-$(date -u +%F).tar.zst && echo ok'
ssh kdkadmin@10.1.20.22 'sudo cat /mnt/backup/dkr/.3d-prints-last-sync'               # the laptop marker, copied by pack
ssh kdkadmin@10.1.20.22 'cat /mnt/backup/dkr/.offsite-status'                          # rc=0
```

Pushover shows `kdk-lab backup OK` mentioning the 3d-prints archive (or the usual
warnings, none about 3d-prints).

## Verify

| Check | Where | Pass |
|---|---|---|
| `findmnt /mnt/3d-prints` | dkr-04 | `nfs4 172.16.30.2:/mnt/tankz3/3d-prints ro,...,vers=4.1` |
| `findmnt /mnt/3d-prints` (after `ls` triggers it) | laptop | `cifs //10.1.3.5/3d-prints` |
| `systemctl --user list-timers cad-sync.timer` | laptop | next run < 15 min |
| `journalctl --user -u cad-sync -n 5` | laptop | no `rsync failed` lines |
| `cat /mnt/3d-prints/.last-sync` | laptop | today, `mbp-dev` |
| storage-health log | Komodo | `mounts:` list includes `3d-prints` |
| `backup-verify` | Pushover / Komodo log | no `3d-prints` FAIL or WARN |

`backup-verify` (runs on kdk-dkr-01, sees `/mnt/backup/dkr` only) **fails** the night if
`offsite/3d-prints-<today>.tar.zst` is missing, empty or fails `zstd -t`, and **warns**
if the laptop marker `.3d-prints-last-sync` (copied with its mtime by `pack`) is missing
or older than 14 days. A stale marker usually just means the laptop was away or asleep.

## Validate offsite from the laptop

Proves the Proton copy is real: the names decrypt, the bytes match the NAS, the archives
open. `hosts/mbp-dev/offsite-mount.sh` (installed at `~/.local/bin/offsite-mount.sh`)
mounts the crypt remote **read-only** at `~/mnt/offsite`; first validated 2026-11-16
(3d-prints archive, kdk-mon-01 archive and the authelia dump matched the NAS sha256).

**Why it has its own Proton session.** Proton refresh tokens are single-use: every
rclone client that refreshes writes the new token back to its own `rclone.conf` and the
old one dies. kdk-dkr-04's `/opt/kdk-lab/backup/rclone/rclone.conf` is the nightly
upload's session; a copy of it on the laptop would race it (`Code=10013`) and break the
next `backup-offsite` run, or force a reseed. So the laptop has its **own** `[proton]`
remote in `~/.config/rclone/rclone.conf` (its own login, `rclone config`), and **never**
a copy of dkr-04's file. The crypt remote is not in any config: the script builds it in
its own environment from 1Password `kdk-ops` / `proton-mirror-crypt` (field `password`),
so the key that decrypts every backup never touches the laptop disk.

On the laptop (`op` signed in; pick a night whose `offsite rclone exit=0`, see the
Grafana **Backups / Nightly backups** dashboard, "On Proton"):

```sh
offsite-mount.sh                                   # 1Password prompt, then: mounted ~/mnt/offsite (read-only, auto-unmount after 2h)
offsite-mount.sh status                            # mounted: ~/mnt/offsite (since ..., auto-unmount after 2h)
ls ~/mnt/offsite/offsite/                          # decrypted names: <host>-DATE.tar.zst, 3d-prints-DATE.tar.zst
D=YYYY-MM-DD

# 1. bytes match the NAS (Proton side streams through the mount; start with the small files)
sha256sum ~/mnt/offsite/offsite/3d-prints-$D.tar.zst ~/mnt/offsite/offsite/kdk-mon-01-$D.tar.zst \
          ~/mnt/offsite/kdk-dkr-dmz-01/pgdump/authelia-$D.sql.gz
ssh kdkadmin@10.1.20.22 "sudo sha256sum /mnt/backup/dkr/offsite/3d-prints-$D.tar.zst \
          /mnt/backup/dkr/offsite/kdk-mon-01-$D.tar.zst /mnt/backup/dkr/kdk-dkr-dmz-01/pgdump/authelia-$D.sql.gz"
                                                   # same three hashes

# 2. the archives open
zstd -t -q ~/mnt/offsite/offsite/3d-prints-$D.tar.zst && echo zstd-ok
gzip -t ~/mnt/offsite/kdk-dkr-dmz-01/pgdump/authelia-$D.sql.gz && echo gzip-ok

# 3. done
offsite-mount.sh -u                                # unmounted ~/mnt/offsite
```

**Unmount when done; it unmounts itself after 2 h anyway.** A mount left up is a
trap: on 2026-11-16 an unrelated whole-disk search (`bfs / -name ...`) walked into
`~/mnt/offsite` and through ~10k Proton files, and every directory it opened was a Proton
API call plus two layers of decryption: rclone sat at 260% CPU until the mount was
stopped. The script therefore runs rclone under `timeout` (`OFFSITE_MAXRUN`, default
`2h`; e.g. `OFFSITE_MAXRUN=30m offsite-mount.sh`). `-u` unmounts, falls back to a lazy
unmount when something holds the mount busy, then stops rclone itself (TERM, KILL after
10 s): a lazily detached rclone otherwise keeps serving whatever still has a directory
open. If the CPU spikes with the mount up, `offsite-mount.sh -u` is the fix.

`--vfs-cache-mode off`: every read streams from Proton, so hashing the ~5 GB
kdk-dkr-01 archive takes a while; the small files above are the routine check. The
mount log is `~/.cache/offsite-mount.log`. Besides `offsite/`, `pve-templates/` and
`*/pgdump/`, the mount also shows the host folders frozen before 2026-10-31 and the
`_archive/` days of the old whole-tree sync: leave them (see
[Backups → Observability](../services/backups.md#observability)).

## Restore

Never extract over `~/cad` directly: restore into a scratch folder and copy back what
you need.

**From a NAS snapshot** (last 2 days hourly, 2 weeks daily). On the NAS:

```sh
sudo zfs list -t snapshot -o name,creation -s creation tankz3/3d-prints | tail
S=auto-YYYY-MM-DD_HH-MM
sudo ls /mnt/tankz3/3d-prints/.zfs/snapshot/$S/
sudo mkdir -p /mnt/tankz3/3d-prints/_restore/$S
sudo rsync -rt /mnt/tankz3/3d-prints/.zfs/snapshot/$S/<project>/ /mnt/tankz3/3d-prints/_restore/$S/<project>/
```

The copy inherits the share ACL, so the laptop sees it at `/mnt/3d-prints/_restore/`;
copy what you need into `~/cad`, then delete `_restore/` over SMB
(`rm -r /mnt/3d-prints/_restore`). cad-sync never touches it (no `--delete`).

**From the local offsite archive** (newest 3 nights, on the backup share). On the laptop:

```sh
ssh kdkadmin@10.1.20.22 'sudo ls -1 /mnt/backup/dkr/offsite/ | grep ^3d-prints-'
mkdir -p ~/cad-restore
ssh kdkadmin@10.1.20.22 'sudo cat /mnt/backup/dkr/offsite/3d-prints-YYYY-MM-DD.tar.zst' | tar --zstd -x -C ~/cad-restore
ls ~/cad-restore/3d-prints/
```

**From Proton** (NAS or backup share lost). On kdk-dkr-04, outside the nightly window
(03:20 until the `photos` stage has written `.photos-status-proton`, [[photo-offsite]]): `rclone.conf` holds the live Proton session
and two rclone runs at once race its single-use refresh token:

```sh
sudo docker run --rm -v /opt/kdk-lab/backup/rclone:/config rclone/rclone:1.76.0 --config /config/rclone.conf lsf proton-crypt:offsite/ --include '3d-prints-*'
sudo mkdir -p /tmp/cad-restore
sudo docker run --rm -v /opt/kdk-lab/backup/rclone:/config -v /tmp/cad-restore:/out rclone/rclone:1.76.0 --config /config/rclone.conf copy proton-crypt:offsite/3d-prints-YYYY-MM-DD.tar.zst /out/
```

Then on the laptop:
`ssh kdkadmin@10.1.20.22 'sudo cat /tmp/cad-restore/3d-prints-YYYY-MM-DD.tar.zst' | tar --zstd -x -C ~/cad-restore`,
and `ssh kdkadmin@10.1.20.22 'sudo rm -r /tmp/cad-restore'`.

## Rollback

1. Revert the lab PR, ResourceSync, Deploy `storage-health-dkr04` (`3d-prints`
   dropped from `NFS_MOUNTS`) **before** unmounting dkr-04, or storage-health pages
   MISSING and the next `backup-verify` fails on the missing archive.
2. Laptop: `systemctl --user disable --now cad-sync.timer`, then
   `sudo systemctl disable --now 'mnt-3d\x2dprints.automount' 'mnt-3d\x2dprints.mount'`,
   remove the two units, `/etc/cifs/3d-prints.cred`, `~/.local/bin/cad-sync.sh` and the
   user units. `~/cad` is untouched by all of this.
3. dkr-04: `sudo systemctl disable --now 'mnt-3d\x2dprints.mount' && sudo rm '/etc/systemd/system/mnt-3d\x2dprints.mount' && sudo systemctl daemon-reload`.
4. NAS: `sharing.nfs.delete <id>`, `pool.snapshottask.delete <id>` (both tasks), then
   `user.delete` / `group.delete` on the `cad-sync` ids and archive the 1Password item.
   The dataset and SMB share stay (they predate this). The extra ACL entry is harmless;
   to drop it, re-run 1d's `setacl` with `select(.tag != "USER" or .id != 3011)` and no
   added entry.
5. Old `3d-prints-*.tar.zst` archives no longer rotate after the revert: delete them by
   hand from `/mnt/backup/dkr/offsite/` (the next offsite sync then removes them from
   Proton).

## Follow-up (not in scope)

Sync over Tailscale when away from home (the laptop's Tailscale is stopped today, and
the share is bound to LAN addresses only). Tracked in the vault `10.Homelab/TODO.README.md`.
