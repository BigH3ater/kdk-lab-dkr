---
title: linux-tools share
updated: 2026-11-04
---

# linux-tools share

NFS share `vmpool/linux-tools` on kdk-nas-01, mounted only by kdk-dkr-04 at
`/mnt/linux-tools`. It holds **portable build kits**: CLI tools, certs, confs and
fetched artifacts that must outlive a laptop or a session scratchpad. First tenant:
**thud** (the MackTrack treadmill HUD). The kit scripts (`tm-adb.sh`, `tm-fetch.sh`,
`tm-install.sh`, `tm-smoke.sh`) live in the `BigH3ater/thud` fork under `kdk/`, are
cloned to `thud/src/`, and read `KIT=${KIT:-/mnt/linux-tools/thud}`.

**Related:** [Storage network & NFS](storage-network.md) · [[kdk-dkr-04]] ·
[Treadmill bridge](treadmill-glassos-bridge.md)

| Item | Value |
|---|---|
| Dataset | `vmpool/linux-tools` (SSD), mountpoint `/mnt/vmpool/linux-tools`, no snapshot task (re-seedable) |
| Identity | NAS user + group `linux-tools`, uid/gid **3010** (1000 is `tdarr-smb`/`media-rw`: never use it) |
| Export | `hosts = ["172.16.30.52"]`, RW, `mapall_user/group = linux-tools` (kdkadmin and root on dkr-04 both write as 3010) |
| Client | kdk-dkr-04, unit `mnt-linux\x2dtools.mount`, `nfsvers=4.1,noatime,_netdev` (`hosts/kdk-dkr-04/systemd/`) |
| Health | `storage-health-dkr04`, `NFS_MOUNTS=media backup linux-tools` |

Because of mapall every local user on dkr-04 maps to 3010, so the 0700/0600 modes
below fence the share from other NFS identities only, not from other dkr-04 users.
Any container that bind-mounts `/mnt` can read `thud/secrets/`; keep such binds
read-only and to `stat`-only jobs (storage-health).

## Layout

| Path (under `/mnt/linux-tools/`) | Mode | Holds |
|---|---|---|
| `.` | 0750 | dataset root |
| `platform-tools/37.0.1/` | 0755 | unpacked r37.0.1 zip + `SHA256SUMS` (Debian adb is too old for `adb pair`) |
| `thud/src/` | 0755 | clone of the fork, `feat/kdk-integration` (scripts in `kdk/`) |
| `thud/secrets/` | 0700 | `glassos_ca.pem` (1265 B), `glassos_client_cert.pem` (1090 B), `glassos_client_key.pem` (1704 B), `kdk-ha.conf` (HA webhook id); files 0600 |
| `thud/vendor/` | 0750 | `NordicFTMS-1.1.3.apk` + `.sha256` (cert source) |
| `thud/conf/` | 0755, files 0644 | `kdk-launcher.conf` (not secret) |
| `thud/apk/<ver>/` | 0755, files 0644 | release APK + `.sha256`; symlinks `current`, `previous`; tm-fetch keeps the newest 10 |
| `thud/backup/` | 0700, files 0600 | app-data tar before each install, newest 5 |
| `thud/state/` | 0700 | last good adb connect port, smoke logs |

Nothing from `secrets/`, `vendor/`, `backup/`, `state/` or any `*.apk`/`*.pem` is ever
committed to the lab repo or the fork.

## Pins

| Artifact | Source | sha256 |
|---|---|---|
| platform-tools r37.0.1 | `https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip` | `d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1` |
| NordicFTMS v1.1.3 | `https://github.com/mikepugh/NordicFTMS/releases/download/v1.1.3/NordicFTMS.apk` | `e83853c45b19b2c87dd384dafd6cde251586e2bee79dbfc9f7a28eb55abd9a39` |

## Order

1. NAS: user, dataset, export (below). 2. dkr-04: install + enable the unit, verify.
3. Merge the lab PR (ResourceSync sets `NFS_MOUNTS`), then run storage-health.
4. Seed the share. Never merge step 3 before step 2 verifies: storage-health would page
`/mnt/linux-tools MISSING` every hour.

## 1. NAS (owner, `ssh kdkadmin@10.1.3.5`)

```sh
sudo midclt call user.query '[["uid","=",3010]]'; sudo midclt call group.query '[["gid","=",3010]]'   # both []
sudo midclt call group.create '{"name":"linux-tools","gid":3010,"smb":false}'
G=$(sudo midclt call group.query '[["group","=","linux-tools"]]' | jq '.[0].id')
sudo midclt call user.create "$(jq -nc --argjson g "$G" '{username:"linux-tools",full_name:"linux-tools NFS share (kdk-dkr-04)",uid:3010,group:$g,group_create:false,home:"/var/empty",shell:"/usr/sbin/nologin",password_disabled:true,smb:false}')"
sudo midclt call pool.dataset.create '{"name":"vmpool/linux-tools","comments":"portable build kits, NFS to kdk-dkr-04 only (docs/runbooks/linux-tools-share.md)"}'
sudo midclt call -j filesystem.setperm '{"path":"/mnt/vmpool/linux-tools","mode":"750","uid":3010,"gid":3010}'
sudo midclt call sharing.nfs.create '{"path":"/mnt/vmpool/linux-tools","comment":"linux-tools build kits (kdk-dkr-04 only)","hosts":["172.16.30.52"],"ro":false,"mapall_user":"linux-tools","mapall_group":"linux-tools"}'
sudo showmount -e localhost | grep linux-tools     # /mnt/vmpool/linux-tools 172.16.30.52
```

No `maproot_*`: TrueNAS refuses maproot together with mapall, and mapall already
covers root. Run `showmount` on the NAS: from dkr-04 it fails by design
(`RPC: Unable to send; errno = Operation not permitted`), the storage fence allows
only 111/2049 to the NAS and mountd listens elsewhere. NFSv4.1 needs only 2049.

## 2. kdk-dkr-04 mount (owner)

From a repo checkout:

```sh
scp 'hosts/kdk-dkr-04/systemd/mnt-linux\x2dtools.mount' kdkadmin@10.1.20.22:/tmp/
ssh kdkadmin@10.1.20.22
sudo install -m 644 '/tmp/mnt-linux\x2dtools.mount' /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now 'mnt-linux\x2dtools.mount' && findmnt /mnt/linux-tools
rm '/tmp/mnt-linux\x2dtools.mount'
```

The unit has no `nofail` (same as media/backup): enable it only after step 1's export
exists. A rebuild enables it from cloud-init on its own `runcmd` line (`|| true`).

## 3. storage-health

After merge and ResourceSync: Komodo UI, Stacks, `storage-health-dkr04`, Deploy (or
wait for the hourly `storage-health` procedure). Log line:
`storage-health OK (172.16.30.2, mounts: media backup linux-tools)`.

## 4. Seed (owner)

Each block names where it runs. The dkr-04 blocks are separate SSH sessions, so each
sets `K` again; the laptop blocks use full paths and need `op` signed in.

**4a. Layout (dkr-04 as kdkadmin)**

```sh
K=/mnt/linux-tools
install -d -m 755 $K/platform-tools $K/platform-tools/37.0.1 $K/thud $K/thud/conf $K/thud/apk
install -d -m 750 $K/thud/vendor
install -d -m 700 $K/thud/secrets $K/thud/backup $K/thud/state
```

**4b. platform-tools (dkr-04 as kdkadmin)** — dkr-04 has no `unzip`; python keeps the exec
bits and strips the top-level `platform-tools/` folder:

```sh
K=/mnt/linux-tools
cd /tmp && curl -fLO https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip
echo 'd230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1  platform-tools_r37.0.1-linux.zip' | sha256sum -c
python3 -I - platform-tools_r37.0.1-linux.zip $K/platform-tools/37.0.1 <<'PY'
import os, sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as z:
    for i in z.infolist():
        rel = i.filename.partition("/")[2]
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            continue
        out = os.path.join(dst, rel)
        if i.is_dir():
            os.makedirs(out, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with z.open(i) as r, open(out, "wb") as w:
            w.write(r.read())
        os.chmod(out, (i.external_attr >> 16) & 0o755 or 0o644)
PY
(cd $K/platform-tools/37.0.1 && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS)
rm platform-tools_r37.0.1-linux.zip
$K/platform-tools/37.0.1/adb version     # Version 37.0.1-...
```

**4c. NordicFTMS APK + the GlassOS certs it carries (dkr-04 as kdkadmin)**

```sh
K=/mnt/linux-tools
cd $K/thud/vendor
curl -fL -o NordicFTMS-1.1.3.apk https://github.com/mikepugh/NordicFTMS/releases/download/v1.1.3/NordicFTMS.apk
echo 'e83853c45b19b2c87dd384dafd6cde251586e2bee79dbfc9f7a28eb55abd9a39  NordicFTMS-1.1.3.apk' | tee NordicFTMS-1.1.3.apk.sha256 | sha256sum -c
chmod 644 NordicFTMS-1.1.3.apk NordicFTMS-1.1.3.apk.sha256
python3 -I - NordicFTMS-1.1.3.apk $K/thud/secrets <<'PY'
import os, sys, zipfile
apk, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk) as z:
    for n in z.namelist():
        if n.startswith("assets/certs/glassos_") and n.endswith(".pem"):
            fd = os.open(os.path.join(dst, os.path.basename(n)), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as w:
                w.write(z.read(n))
PY
stat -c '%n %s %a' $K/thud/secrets/glassos_*.pem    # 1265 / 1090 / 1704, all 600
```

**4d. `kdk-ha.conf` (secret) and `kdk-launcher.conf` (not secret, but it names LAN
hostnames) — on the LAPTOP, not in the dkr-04 session** (`op` lives there; the
`ssh ... install` writes land on the share). The 1Password Documents `thud-kdk-ha-conf`
and `thud-kdk-launcher-conf` (vault `kdk-cluster`) are the source of truth for both
files; the share copy is a cache. Neither file is in the fork or the lab repo:

```sh
# laptop as jmack (op + ssh): seed both confs on the share from 1Password
op document get thud-kdk-ha-conf --vault kdk-cluster | ssh kdkadmin@10.1.20.22 'install -m 600 /dev/stdin /mnt/linux-tools/thud/secrets/kdk-ha.conf'
op document get thud-kdk-launcher-conf --vault kdk-cluster | ssh kdkadmin@10.1.20.22 'install -m 644 /dev/stdin /mnt/linux-tools/thud/conf/kdk-launcher.conf'
ssh kdkadmin@10.1.20.22 'stat -c "%n %s %a" /mnt/linux-tools/thud/secrets/kdk-ha.conf /mnt/linux-tools/thud/conf/kdk-launcher.conf'   # 86 600, 292 644
```

To change the Apps-button targets, edit the 1Password document `thud-kdk-launcher-conf`
and rerun the block; the next `kdk/tm-install.sh` places the new file (it only rewrites
a conf whose sha256 changed).

**4e. Fork clone (dkr-04 as kdkadmin)** — files on the share are owned by 3010, so git needs
`safe.directory` for kdkadmin:

```sh
K=/mnt/linux-tools
git clone -b feat/kdk-integration https://github.com/BigH3ater/thud $K/thud/src
git config --global --add safe.directory $K/thud/src
git -C $K/thud/src log -1 --oneline
```

## Verify

**On kdk-dkr-04 as kdkadmin** (`ssh kdkadmin@10.1.20.22`)

```sh
findmnt /mnt/linux-tools                       # nfs4, 172.16.30.2:/mnt/vmpool/linux-tools, vers=4.1
touch /mnt/linux-tools/.rw && stat -c '%u:%g' /mnt/linux-tools/.rw && rm /mnt/linux-tools/.rw   # 3010:3010
stat -c '%n %a' /mnt/linux-tools /mnt/linux-tools/thud/secrets /mnt/linux-tools/thud/vendor /mnt/linux-tools/thud/state   # 750 700 750 700
stat -c '%n %s %a' /mnt/linux-tools/thud/secrets/*     # pem sizes 1265/1090/1704, all 600
(cd /mnt/linux-tools/thud/vendor && sha256sum -c NordicFTMS-1.1.3.apk.sha256)
(cd /mnt/linux-tools/platform-tools/37.0.1 && sha256sum -c --quiet SHA256SUMS) && /mnt/linux-tools/platform-tools/37.0.1/adb version
```

Plus the storage-health OK line listing three mounts (step 3).

## Rollback

1. Revert the lab PR, ResourceSync, Deploy `storage-health-dkr04` (back to
   `media backup`) **before** unmounting, or storage-health pages MISSING.
2. dkr-04: `sudo systemctl disable --now 'mnt-linux\x2dtools.mount' && sudo rm '/etc/systemd/system/mnt-linux\x2dtools.mount' && sudo systemctl daemon-reload`.
3. NAS (destroys the kit; everything in it is re-seedable from the pins and 1Password):
   `sudo midclt call sharing.nfs.delete <id>` (id from `sharing.nfs.query '[["path","=","/mnt/vmpool/linux-tools"]]'`),
   `sudo midclt call pool.dataset.delete vmpool/linux-tools`, then `user.delete` /
   `group.delete` on the `linux-tools` ids.
4. Seed only: `rm -r` the affected subdirectory and re-run its step.
