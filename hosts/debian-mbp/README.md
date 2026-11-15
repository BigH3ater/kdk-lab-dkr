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

Not in git: `/etc/cifs/3d-prints.cred` (root 0600, `username=` + `password=` lines),
written from 1Password `kdk-ops/kdk-nas-01-cad-sync-smb` by runbook step 3b.

Packages the units need: `cifs-utils`, `rsync` (neither was installed on 2026-11-15).

Check:

```sh
systemctl status 'mnt-3d\x2dprints.automount' --no-pager
systemctl --user list-timers cad-sync.timer
journalctl --user -u cad-sync -n 20 --no-pager
cat /mnt/3d-prints/.last-sync
```
