# hosts/debian-mbp -- the owner's laptop (applied by hand)

Debian laptop `debian-mbp` (wifi `10.1.30.188`), the admin workstation and the place
the CAD library `~/cad` is edited. Not a lab server and not a Komodo host: nothing here
is deployed automatically, and none of it is in cloud-init. Runbook:
`docs/runbooks/cad-library.md` (install order, verify, restore, rollback).

| Path | Installs to | What |
|---|---|---|
| `systemd/mnt-3d\x2dprints.mount` | `/etc/systemd/system/` (root 0644) | cifs mount of `//10.1.3.5/3d-prints` at `/mnt/3d-prints` as NAS user `cad-sync` (`vers=3.1.1,seal`, uid/gid 1000 = jmack), no `[Install]` |
| `systemd/mnt-3d\x2dprints.automount` | `/etc/systemd/system/` (root 0644) | mounts on first access, unmounts after 10 idle min; the only unit enabled, so boot never waits for the NAS |
| `cad-sync.sh` | `~/.local/bin/cad-sync.sh` (0755) | `rsync -rt --modify-window=2` `~/cad/` → `/mnt/3d-prints/`, no `--delete`; exits 0 silently when the NAS is unreachable; writes `.last-sync` on success; `--dry-run` previews |
| `systemd-user/cad-sync.{service,timer}` | `~/.config/systemd/user/` (0644) | runs the script every 15 min (`OnCalendar=*:0/15`, `Persistent=true`) while jmack is logged in |
| `offsite-mount.sh` | `~/.local/bin/offsite-mount.sh` (0755) | read-only `rclone mount` of the Proton offsite (`kdk-lab-backup`, crypt) at `~/mnt/offsite`, using the laptop's **own** `proton` remote; the crypt key is read from 1Password at mount time and lives only in the process env. unmounts itself after `OFFSITE_MAXRUN` (default 2 h); `-u` unmounts now (lazy + stops rclone if busy), `status` reports. Use: runbook section "Validate offsite from the laptop" |

`cad-sync.sh` and `offsite-mount.sh` are copied by hand: after a change here, run
`install -m 0755 hosts/debian-mbp/<file> ~/.local/bin/` (e.g. the `.~*.lock` exclude for
SolidCraft lock files in `cad-sync.sh`).

Not in git: `/etc/cifs/3d-prints.cred` (root 0600, `username=` + `password=` lines),
written from 1Password `kdk-ops/kdk-nas-01-cad-sync-smb` by runbook step 3b.

Packages the units need: `cifs-utils`, `rsync` (neither was installed on 2026-11-15).
`offsite-mount.sh` needs `fuse3` (`fusermount3`), the 1Password CLI `op`, and rclone at
`~/.local/bin/rclone` (or `RCLONE=`), with a `[proton]` remote in `~/.config/rclone/rclone.conf`
made on this laptop (`rclone config`, its own Proton login), never a copy of kdk-dkr-04's.

Check:

```sh
systemctl status 'mnt-3d\x2dprints.automount' --no-pager
systemctl --user list-timers cad-sync.timer
journalctl --user -u cad-sync -n 20 --no-pager
cat /mnt/3d-prints/.last-sync
```
