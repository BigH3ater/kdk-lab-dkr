# backup-offsite

Nightly one-shot on kdk-dkr-04 (the `offsite` stage of the `nightly-backups`
Procedure; moved off kdk-dkr-01 in migration B1 so the pack+rclone burst runs on
the NAS VM, where `/mnt/backup` is local). Two services run in order: **pack**
writes one `tar.zst` per host
into `/mnt/backup/dkr/offsite/` (`pack.sh`), then **offsite** `rclone sync`s the
offsite set to the Proton crypt remote (`offsite.sh`). See
[docs/backups.md](../../docs/backups.md) for the full pipeline and the pack
exclude list.

## What goes to Proton

Only `offsite/**` (the per-host archives) + `pve-templates/**` (the Proxmox
template vzdump) + every host's `*/pgdump/**`. The scope is set by `--filter`
rules in `offsite.sh` (all `--filter`, never mixed `--include`/`--exclude`, so
their order is deterministic).

## Design decisions (don't undo these)

- **Writable, persisted `rclone.conf`.** `offsite.sh` runs rclone against
  `/config/rclone.conf` directly (mounted **rw** from
  `/opt/kdk-lab/backup/rclone`). The protondrive backend logs in once from
  `username` + `password` + `otp_secret_key` (it computes the TOTP itself) and
  caches the session (`client_uid`, `client_access_token`,
  `client_refresh_token`, `client_salted_key_pass`) back into the file. Those
  cached tokens must survive between passes and between nights. **Do not** copy
  the config to a throwaway path or log in fresh per pass: Proton rejects a
  repeated login that reuses an already-spent one-time 2FA with
  `422 ... Code=8002`.
- **Low concurrency** (`--transfers 2 --checkers 4`) so rclone's workers don't
  race on Proton's single-use refresh token (`Code=10013`).
- **No `--backup-dir`.** The sync root is the whole remote, so any backup-dir
  prefix overlaps the destination and rclone aborts with exit 7
  (`destination and parameter to --backup-dir mustn't overlap`). Generational
  history already lives on the NAS (`pack` keeps the newest 3 archives per host;
  each pg_dump keeps 15), so the offsite is a plain mirror.

## Reseed the Proton session by hand (only if it is fully dead)

If the cached session has expired (e.g. the stage hasn't run for weeks) rclone
will try to re-login automatically from the stored `otp_secret_key`; that is the
normal path and needs no action. If Proton still refuses (e.g. the account
password or 2FA secret changed), reconnect once on kdk-dkr-04 (the host that now
runs this stage) — this rewrites the `client_*` fields in place and prints no
secret values:

```bash
sudo docker run --rm -it \
  -v /opt/kdk-lab/backup/rclone:/config \
  rclone/rclone:1.75.2 --config /config/rclone.conf config reconnect proton:
```

If `username`/`password`/`otp_secret_key` themselves are stale, re-enter them
from the Proton account entry in 1Password (the crypt password is
`proton-mirror-crypt` in the `kdk-ops` vault; the `[proton-crypt]` remote stays
untouched) with `… config update proton: username <...> password <...>
otp_secret_key <...>`, then run the `reconnect` above. Never commit `rclone.conf`.
