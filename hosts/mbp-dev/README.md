# hosts/mbp-dev -- the owner's laptop (applied by hand)

Debian 13 laptop `mbp-dev` (MacBookPro11,4, wifi `10.1.30.188`), the admin workstation
and the place the CAD library `~/cad` is edited. It was `debian-mbp` (Debian 12) until
the 2026-11-19 reinstall. Not a lab server and not a Komodo host: nothing here is
deployed automatically, and none of it is in cloud-init. Runbooks:
`docs/runbooks/cad-library.md` (CAD library), `docs/runbooks/laptop-backup.md` (restic
backup), `docs/runbooks/laptop-rebuild.md` (the reinstall).

`install.sh` is the post-install setup in one script (run as jmack from a checkout of
this repo, it calls sudo, safe to re-run): base packages, the Docker / Tailscale /
VS Code / Signal / WezTerm / 1Password apt repositories, Bambu Studio (Flatpak), tlp + thermald + mbpfan, wifi
profile settings, every unit below with its credential files from 1Password, and Node.js 24 per-user. Sign
in to 1Password (desktop app + CLI integration) first: the units section stops with a
message when `op read` cannot read `kdk-ops`. It never runs `tailscale up` and never
touches LUKS.

| Path | Installs to | What |
|---|---|---|
| `install.sh` | run in place (0755) | post-install setup, sections a–g (see above); `bash hosts/mbp-dev/install.sh` |
| `systemd/mnt-3d\x2dprints.mount` | `/etc/systemd/system/` (root 0644) | cifs mount of `//10.1.3.5/3d-prints` at `/mnt/3d-prints` as NAS user `cad-sync` (`vers=3.1.1,seal`, uid/gid 1000 = jmack), no `[Install]` |
| `systemd/mnt-3d\x2dprints.automount` | `/etc/systemd/system/` (root 0644) | mounts on first access, unmounts after 10 idle min; the only unit enabled, so boot never waits for the NAS |
| `cad-sync.sh` | `~/.local/bin/cad-sync.sh` (0755) | `rsync -rt --modify-window=2` `~/cad/` → `/mnt/3d-prints/`, no `--delete`; exits 0 silently when the NAS is unreachable; writes `.last-sync` on success; `--dry-run` previews |
| `systemd-user/cad-sync.{service,timer}` | `~/.config/systemd/user/` (0644) | runs the script every 15 min (`OnCalendar=*:0/15`, `Persistent=true`) while jmack is logged in |
| `offsite-mount.sh` | `~/.local/bin/offsite-mount.sh` (0755) | read-only `rclone mount` of the Proton offsite (`kdk-lab-backup`, crypt) at `~/mnt/offsite`, using the laptop's **own** `proton` remote; the crypt key is read from 1Password at mount time and lives only in the process env. unmounts itself after `OFFSITE_MAXRUN` (default 2 h); `-u` unmounts now (lazy + stops rclone if busy), `status` reports. Use: runbook section "Validate offsite from the laptop" |
| `systemd/mnt-laptop\x2dbackup.mount` | `/etc/systemd/system/` (root 0644) | cifs mount of `//10.1.3.5/laptop-backup` at `/mnt/laptop-backup` as NAS user `laptop-backup` (`vers=3.1.1,seal`, uid/gid 0, 0600/0700), no `[Install]` |
| `systemd/mnt-laptop\x2dbackup.automount` | `/etc/systemd/system/` (root 0644) | mounts on first access, unmounts after 10 idle min; enabled |
| `laptop-backup.sh` | `/usr/local/sbin/laptop-backup.sh` (root 0755) | `restic backup /home/jmack /bench /etc --host mbp-dev` to `/mnt/laptop-backup/restic`, then `forget` (7 d / 4 w / 12 m, `pre-wipe` kept); prune Sundays, check on the 1st; exits 0 with one line when the NAS is unreachable; `--tag`, `--prune`, `--check` |
| `laptop-backup.excludes` | `/etc/restic/laptop-backup.excludes` (root 0644) | restic excludes (Downloads, caches, trash, `~/mnt`, browser/VS Code caches, `~/.docker/desktop`) |
| `systemd/laptop-backup.{service,timer}` | `/etc/systemd/system/` (root 0644) | system oneshot as root, `Nice=10`, idle I/O; timer daily 12:30, `Persistent=true`, 10 min random delay |

`cad-sync.sh` and `offsite-mount.sh` are copies: after a change here, run
`install -m 0755 hosts/mbp-dev/<file> ~/.local/bin/` (or `install.sh e`) (e.g. the `.~*.lock` exclude for
SolidCraft lock files in `cad-sync.sh`).

Not in git: `/etc/cifs/3d-prints.cred` (root 0600, `username=` + `password=` lines),
written from 1Password `kdk-ops/kdk-nas-01-cad-sync-smb` by runbook step 3b.
`/etc/cifs/laptop-backup.cred` (root 0600) from `kdk-ops/kdk-nas-01-laptop-backup-smb`
and `/etc/restic/laptop-backup.pass` (root 0600) from `kdk-ops/mbp-dev-restic-repo`,
both written by laptop-backup.md step 2b. `laptop-backup.sh` is installed with
`sudo install -m 755 -o root -g root hosts/mbp-dev/laptop-backup.sh /usr/local/sbin/`.

Packages the units need: `cifs-utils`, `rsync`, `restic` (0.18 on Debian 13), all
installed by `install.sh`.
`offsite-mount.sh` needs `fuse3` (`fusermount3`), the 1Password CLI `op`, and rclone at
`~/.local/bin/rclone` (or `RCLONE=`), with a `[proton]` remote in `~/.config/rclone/rclone.conf`
made on this laptop (`rclone config`, its own Proton login), never a copy of kdk-dkr-04's.

Check:

```sh
systemctl status 'mnt-3d\x2dprints.automount' --no-pager
systemctl --user list-timers cad-sync.timer
journalctl --user -u cad-sync -n 20 --no-pager
cat /mnt/3d-prints/.last-sync
systemctl list-timers laptop-backup.timer
journalctl -u laptop-backup -n 20 --no-pager      # laptop-backup: backup OK snapshot=<id> rc=0
```
