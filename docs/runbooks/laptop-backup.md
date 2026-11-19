---
title: Laptop backup (restic → NAS)
updated: 2026-11-19
---

# Laptop backup

A daily `restic` backup of the owner's laptop (`debian-mbp` today, `mbp-dev` after the
[rebuild](laptop-rebuild.md)) to a dedicated SMB share on kdk-nas-01. It covers
`/home/jmack`, `/bench` and `/etc`; the repository is encrypted by restic, and the NAS
snapshots the dataset daily so a laptop that deletes or corrupts the repository cannot
take the history with it.

**Related:** [[kdk-nas-01]] · [CAD library](cad-library.md) (same laptop/SMB pattern) · [Laptop rebuild](laptop-rebuild.md) · `hosts/debian-mbp/README.md`

```text
laptop /home/jmack /bench /etc --laptop-backup.sh (root, daily 12:30, restic)--> SMB //10.1.3.5/laptop-backup (user laptop-backup)
                                                                                   /restic = the restic repository (encrypted)
                                                                                   | tankz3/laptop-backup: snapshot daily 04:00, kept 2 weeks
```

| Item | Value |
|---|---|
| Dataset | `tankz3/laptop-backup`, mountpoint `/mnt/tankz3/laptop-backup`, share_type SMB, lz4, atime off, quota **300 GiB** |
| SMB | share id **9** `laptop-backup` → `/mnt/tankz3/laptop-backup`, not browsable |
| Identity | NAS group `laptop-backup` gid **3014**; user `laptop-backup` uid **3014**, primary group 3014, SMB only, shell `nologin`, home `/var/empty`; password in 1Password `kdk-ops/kdk-nas-01-laptop-backup-smb` (Login) |
| ACL | owner root (0), group 3014; `owner@` FULL_CONTROL inherit, `group@` MODIFY inherit; the default builtin-users entries removed |
| Snapshots | task id **20**: daily 04:00, kept 2 WEEK, `auto-%Y-%m-%d_%H-%M` |
| Repository | `/mnt/laptop-backup/restic`, password in 1Password `kdk-ops/mbp-dev-restic-repo` (Password), on the laptop at `/etc/restic/laptop-backup.pass` root 0600 |
| restic host | always `mbp-dev` (`--host`), also before the rename, so the history is one line |
| Laptop mount | `/mnt/laptop-backup`, `mnt-laptop\x2dbackup.automount` + `.mount` (cifs `vers=3.1.1,seal`, uid/gid 0, 0600/0700), creds `/etc/cifs/laptop-backup.cred` root 0600 |
| Laptop job | `/usr/local/sbin/laptop-backup.sh` + system `laptop-backup.service`/`.timer` (daily 12:30, `Persistent=true`, up to 10 min random delay), excludes `/etc/restic/laptop-backup.excludes` (`hosts/debian-mbp/`) |
| Retention | every run `forget --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --keep-tag pre-wipe`; `--prune` on Sundays; `restic check` on the 1st |

### What the backup does and does not do

- **Contains** `/home/jmack` (including `~/.ssh`, `~/.claude`, `~/.config`, `~/cad`,
  `~/Pictures`, every `node_modules` and `.git`), all of `/bench`, and `/etc`. `/etc`
  deliberately includes the saved wifi profile with its key under
  `/etc/NetworkManager/system-connections/` and the two credential files of this job:
  the repository is encrypted, and the repository password is in 1Password, not in the
  repository.
- **Only copy:** the bench repos without a git remote (`habit-tracker`, `kdk-irtracker`,
  `remarkable-ebooks`, `remarkable-highlights`) and the non-git folders
  (`deploy-staging`, `gns3-build`, `pi2-dns-build`, `VOSS`) exist nowhere else; the NAS
  repository is their only copy. No offsite yet (see Follow-up).
- **Excluded** (`laptop-backup.excludes`): `~/Downloads`, `~/.cache`, the trash, `~/mnt`
  (rclone/FUSE mounts), browser and VS Code caches under `~/.config`, flatpak/snap app
  caches, `~/.local/share/containers`. `--exclude-caches` also skips any folder with a
  `CACHEDIR.TAG`; `--one-file-system` never crosses into another mount.
- **Away from home** it exits 0 with one line (`NAS 10.1.3.5 not reachable, skipping`):
  a 3 s TCP probe to `10.1.3.5:445`, then a 30 s-guarded `stat -f` that must say
  cifs/smb2, so it never writes into the bare local `/mnt/laptop-backup` directory.
  restic exit 3 (some files unreadable) still counts as a snapshot.
- **Log:** success is one fixed line in the journal:
  `laptop-backup: backup OK snapshot=<id> rc=0`.
- **Options** are restic 0.14 (Debian 12 apt) only; 0.18 (Debian 13 apt) runs the same.

## Order

1. NAS: dataset, share, ACL, snapshot task (done 2026-11-19); 1Password items, then the user.
2. Laptop: packages, credentials + repository password, automount, script + units, `restic init`.
3. First full backup by hand, tagged `pre-wipe`.
4. Test restore from `pre-wipe`.
5. Enable the timer.

**No wipe of the laptop until step 4 passes.** The [rebuild](laptop-rebuild.md) gates
on it.

## 1. NAS (kdk-nas-01)

**As built 2026-11-19** (TrueNAS 25.10.6). Reference for a rebuild only, do not re-run;
the `group.create` line is verbatim, the others are the equivalent calls for the
recorded result (check field names against the API before reusing them):

```sh
sudo midclt call group.create '{"name":"laptop-backup","gid":3014,"smb":false}'
sudo midclt call pool.dataset.create '{"name":"tankz3/laptop-backup","share_type":"SMB","atime":"OFF","compression":"LZ4","quota":322122547200}'
sudo midclt call pool.snapshottask.create '{"dataset":"tankz3/laptop-backup","recursive":false,"lifetime_value":2,"lifetime_unit":"WEEK","naming_schema":"auto-%Y-%m-%d_%H-%M","allow_empty":true,"enabled":true,"schedule":{"minute":"0","hour":"4","dom":"*","month":"*","dow":"*","begin":"00:00","end":"23:59"}}'   # id 20
sudo midclt call sharing.smb.create '{"path":"/mnt/tankz3/laptop-backup","name":"laptop-backup","browsable":false,"comment":"restic repository of the owner laptop (docs/runbooks/laptop-backup.md)"}'   # id 9
D=/mnt/tankz3/laptop-backup
sudo midclt call -j filesystem.setacl "$(jq -nc --arg p $D '{path:$p,uid:0,gid:3014,acltype:"NFS4",dacl:[
  {tag:"owner@",id:null,type:"ALLOW",perms:{BASIC:"FULL_CONTROL"},flags:{BASIC:"INHERIT"}},
  {tag:"group@",id:null,type:"ALLOW",perms:{BASIC:"MODIFY"},flags:{BASIC:"INHERIT"}}],
  options:{recursive:true,traverse:false,canonicalize:true}}')"
```

Owner-only (root) plus group 3014: the laptop user is the only SMB client, and the
removed builtin-users entries mean no other NAS account can read the repository files.

**1a. Generate both secrets in 1Password (owner, laptop).** Bare `op` on the laptop is
the read-only service account, so writes drop its token. Letters and digits only, so
they are safe in the cifs credentials file:

```sh
env -u OP_SERVICE_ACCOUNT_TOKEN op item create --vault kdk-ops --category login \
  --title kdk-nas-01-laptop-backup-smb --generate-password=letters,digits,32 username=laptop-backup \
  'notesPlain=SMB user for the laptop restic backup to //10.1.3.5/laptop-backup (docs/runbooks/laptop-backup.md)' >/dev/null
env -u OP_SERVICE_ACCOUNT_TOKEN op item create --vault kdk-ops --category password \
  --title mbp-dev-restic-repo --generate-password=letters,digits,40 \
  'notesPlain=restic repository password, //10.1.3.5/laptop-backup/restic (docs/runbooks/laptop-backup.md). Without it the backup is unreadable.' >/dev/null
op read 'op://kdk-ops/kdk-nas-01-laptop-backup-smb/password' | wc -c     # 33 (32 + newline)
op read 'op://kdk-ops/mbp-dev-restic-repo/password' | wc -c              # 41 (40 + newline)
```

**1b. User, password from 1Password over ssh stdin (owner, laptop).** The password never
appears on a command line:

```sh
op read 'op://kdk-ops/kdk-nas-01-laptop-backup-smb/password' | ssh kdkadmin@10.1.3.5 'sudo python3 -c "
import sys
from truenas_api_client import Client
pw=sys.stdin.read().strip()
with Client() as c:
    g=c.call(\"group.query\",[[\"gid\",\"=\",3014]])[0][\"id\"]
    c.call(\"user.create\",{\"username\":\"laptop-backup\",\"full_name\":\"laptop-backup SMB (laptop restic)\",\"uid\":3014,\"group\":g,\"group_create\":False,\"home\":\"/var/empty\",\"shell\":\"/usr/sbin/nologin\",\"smb\":True,\"password\":pw})
    print(\"created\")
"'
```

**1c. Verify (NAS, `ssh kdkadmin@10.1.3.5`)**

```sh
sudo midclt call user.query '[["username","=","laptop-backup"]]' | jq -c '.[0] | {uid,group:.group.bsdgrp_gid,smb,shell,home,locked}'   # 3014, 3014, true, nologin, /var/empty, false
sudo midclt call sharing.smb.query '[["name","=","laptop-backup"]]' | jq -c '.[0] | {id,path,browsable,enabled}'   # 9, /mnt/tankz3/laptop-backup, false, true
sudo midclt call filesystem.getacl /mnt/tankz3/laptop-backup | jq -c '{uid,gid,acl:[.acl[] | [.tag,.perms.BASIC]]}'   # 0, 3014, owner@ FULL_CONTROL, group@ MODIFY
sudo midclt call pool.snapshottask.query '[["dataset","=","tankz3/laptop-backup"]]' | jq -c '.[] | {id,lifetime_value,lifetime_unit,hour:.schedule.hour}'   # 20, 2, WEEK, "4"
sudo zfs get -H -o property,value quota,compression,atime tankz3/laptop-backup     # 300G, lz4, off
```

## 2. Laptop install (owner, on the laptop)

**2a. Packages:**

```sh
sudo apt install restic cifs-utils
restic version                    # 0.14.x on Debian 12, 0.18.x on Debian 13
```

**2b. Credentials file and repository password, read from 1Password in the same step**
(no secret on any command line; both files root 0600):

```sh
sudo install -d -m 700 /etc/cifs /etc/restic
{ echo username=laptop-backup; op read 'op://kdk-ops/kdk-nas-01-laptop-backup-smb/password' | sed 's/^/password=/'; } \
  | sudo install -m 600 -o root -g root /dev/stdin /etc/cifs/laptop-backup.cred
op read -n 'op://kdk-ops/mbp-dev-restic-repo/password' \
  | sudo install -m 600 -o root -g root /dev/stdin /etc/restic/laptop-backup.pass
sudo stat -c '%a %U %s %n' /etc/cifs/laptop-backup.cred /etc/restic/laptop-backup.pass
# 600 root 65 (23 + 42 bytes) /etc/cifs/laptop-backup.cred
# 600 root 40 /etc/restic/laptop-backup.pass
```

**2c. Mount + automount** (from the repo checkout):

```sh
sudo install -m 644 'hosts/debian-mbp/systemd/mnt-laptop\x2dbackup.mount' 'hosts/debian-mbp/systemd/mnt-laptop\x2dbackup.automount' /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now 'mnt-laptop\x2dbackup.automount'
sudo ls -la /mnt/laptop-backup && findmnt /mnt/laptop-backup   # empty; cifs //10.1.3.5/laptop-backup ... vers=3.1.1,seal
sudo touch /mnt/laptop-backup/.rw-test && sudo rm /mnt/laptop-backup/.rw-test && echo writable
```

**2d. Script, excludes and units, do NOT enable the timer yet:**

```sh
sudo install -m 755 -o root -g root hosts/debian-mbp/laptop-backup.sh /usr/local/sbin/laptop-backup.sh
sudo install -m 644 -o root -g root hosts/debian-mbp/laptop-backup.excludes /etc/restic/laptop-backup.excludes
sudo install -m 644 hosts/debian-mbp/systemd/laptop-backup.service hosts/debian-mbp/systemd/laptop-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

**2e. Create the repository (once):**

```sh
sudo restic -r /mnt/laptop-backup/restic --password-file /etc/restic/laptop-backup.pass init
# created restic repository <id> at /mnt/laptop-backup/restic
sudo restic -r /mnt/laptop-backup/restic --password-file /etc/restic/laptop-backup.pass snapshots   # empty list, no error
```

## 3. First full backup, tagged `pre-wipe`

About 40 GB. Plug into the wired Ethernet first (Thunderbolt adapter): over the old
2.4 GHz wifi this takes hours. Stop the laptop sleeping while it runs. The script prints
nothing until restic finishes:

```sh
sudo systemd-inhibit --what=sleep:idle --why='restic pre-wipe' /usr/local/sbin/laptop-backup.sh --tag pre-wipe
# ... snapshot <id> saved
# laptop-backup: backup OK snapshot=<id> rc=0
r() { sudo restic -r /mnt/laptop-backup/restic --password-file /etc/restic/laptop-backup.pass "$@"; }   # shorthand for this shell
r snapshots --tag pre-wipe                      # one snapshot, host mbp-dev, paths /bench /etc /home/jmack
r check --read-data-subset=10%                  # no errors were found
```

`rc=3` means some files could not be read (the output lists them): look at them before
the wipe; anything that matters, fix and run again (it is incremental and fast).
Run step 3 again right before the wipe if work happened since.

## 4. Test restore (gate for the wipe)

Restore the lab repo and `~/.claude` from the `pre-wipe` snapshot into a scratch folder
and compare. **NO WIPE until this passes.**

```sh
ID=<snapshot id from step 3>
T=/var/tmp/laptop-restore-test
r restore "$ID" --target "$T" --include /bench/kdk-lab-dkr --include /home/jmack/.claude
sudo diff -rq "$T/bench/kdk-lab-dkr" /bench/kdk-lab-dkr                # nothing, or only files changed since the snapshot
sudo diff -rq "$T/home/jmack/.claude" /home/jmack/.claude | head       # only session files written since the snapshot
sudo git -c safe.directory='*' -C "$T/bench/kdk-lab-dkr" status --short   # same as `git -C /bench/kdk-lab-dkr status --short`
sudo git -c safe.directory='*' -C "$T/bench/kdk-lab-dkr" fsck --full      # no errors (dangling objects are fine)
sudo stat -c '%U %a %n' "$T/home/jmack/.claude" "$T/bench/kdk-lab-dkr"   # jmack (owners come back)
sudo rm -rf "$T"
```

Pass = no missing files, no content differences in files untouched since the snapshot,
`git status` matches, `git fsck` clean.

## 5. Enable the timer

```sh
sudo systemctl enable --now laptop-backup.timer && systemctl list-timers laptop-backup.timer
sudo systemctl start laptop-backup.service && journalctl -u laptop-backup -n 20 --no-pager
# laptop-backup: backup OK snapshot=<id> rc=0
```

## Verify

`r` = the shorthand function from step 3.

| Check | Where | Pass |
|---|---|---|
| `systemctl list-timers laptop-backup.timer` | laptop | next run today or tomorrow ~12:30 |
| `journalctl -u laptop-backup --since -2d \| grep 'backup OK'` | laptop | a line from the last day the laptop was home |
| `journalctl -u laptop-backup -n 20` | laptop | no `failed` lines; `skipping` when away is fine |
| `r snapshots --host mbp-dev --latest 1` | laptop | recent snapshot |
| `r snapshots --tag pre-wipe` | laptop | still there (kept forever) |
| `zfs list -t snapshot tankz3/laptop-backup` | NAS | a daily `auto-*` from 04:00, 14 of them |
| `zfs get -H used,quota tankz3/laptop-backup` | NAS | used well under 300G |

## Restore on a fresh machine

Needs only: `restic`, `cifs-utils`, and the two 1Password items
(`kdk-ops/kdk-nas-01-laptop-backup-smb`, `kdk-ops/mbp-dev-restic-repo`). Do steps 2a, 2b
and 2c (packages, the two files from 1Password, the automount) from a clone of this repo
(or copy the unit text from GitHub), then:

```sh
r() { sudo restic -r /mnt/laptop-backup/restic --password-file /etc/restic/laptop-backup.pass "$@"; }   # shorthand for this shell
r snapshots                                       # pick the snapshot: newest, or the pre-wipe one
ID=<snapshot id>
T=/var/tmp/laptop-restore
r restore "$ID" --target "$T" \
  --include /home/jmack \
  --include /bench/kdk-lab-dkr --include /bench/thud \
  --include /bench/local_roborock_server --include /bench/network-sre-learning
sudo rsync -aHAX "$T/home/jmack/" /home/jmack/                  # home (uid 1000 = jmack on both installs)
sudo mkdir -p /bench && sudo chown jmack:jmack /bench
sudo rsync -aHAX "$T/bench/" /bench/
sudo rm -rf "$T"
```

- **`/bench` comes back as four folders only:** `kdk-lab-dkr`, `thud`,
  `local_roborock_server`, `network-sre-learning`. The other ten (`deploy-staging`,
  `gns3-build`, `habit-tracker`, `kdk-irtracker`, `kdk-vault-settings`,
  `netops-automation`, `pi2-dns-build`, `remarkable-ebooks`, `remarkable-highlights`,
  `VOSS`, plus the loose `pieeprom.upd`/`recovery.bin`) stay in the `pre-wipe` snapshot
  as an archive; restore one later with `--include /bench/<name>`.
- **`/etc` is not restored wholesale** onto a new OS: pull single files from the
  snapshot as needed, e.g. the wifi profile
  `r dump "$ID" /etc/NetworkManager/system-connections/<ssid>.nmconnection`.
- A single file at any time: `r dump <id> <path> > /tmp/file`, or browse with
  `r ls <id> <dir>`.

**From a NAS snapshot** (repository itself damaged or deleted, last 2 weeks): on the NAS,
`sudo zfs list -t snapshot tankz3/laptop-backup`, then
`sudo zfs clone tankz3/laptop-backup@auto-YYYY-MM-DD_HH-MM tankz3/laptop-backup-restore`
and point `RESTIC_REPOSITORY` at a share of the clone, or (whole repo lost)
`sudo zfs rollback -r tankz3/laptop-backup@auto-...` with the laptop job stopped.

## Rollback

1. Laptop: `sudo systemctl disable --now laptop-backup.timer`, then
   `sudo systemctl disable --now 'mnt-laptop\x2dbackup.automount' 'mnt-laptop\x2dbackup.mount'`;
   remove the four units, `/usr/local/sbin/laptop-backup.sh`, `/etc/restic/` and
   `/etc/cifs/laptop-backup.cred`; `sudo systemctl daemon-reload`. Nothing on the laptop
   is changed by the backup itself.
2. NAS (only if the backups are not wanted at all; this deletes them):
   `sharing.smb.delete 9`, `pool.snapshottask.delete 20`, `zfs destroy -r tankz3/laptop-backup`,
   then `user.delete` / `group.delete` on the `laptop-backup` ids, and archive both
   1Password items.

## Follow-up (not in this PR)

Offsite: sync the already-encrypted repository folder to the Proton offsite (the restic
encryption means no second crypt layer is needed). Until then the NAS (plus its 2-week
snapshots) is the only copy. Add it to the vault `10.Homelab/TODO.README.md`.
