# backup-photos

Nightly one-shot on kdk-dkr-04, the `photos` stage of the `nightly-backups` procedure
(after `offsite`, before `verify`). `photos.sh` syncs the photo **originals** file by file:
Immich `library/`, `upload/`, `profile/` (read-only NFS `/mnt/immich`) and the Insta360
export (`/mnt/insta360-flat`, bound read-only), first to Proton
(`proton-crypt:photos/...`), then to Backblaze B2 (`b2photoscrypt:photos/...`, its own crypt
key; also the Immich DB dumps → `b2photoscrypt:immich-db/`). One remote after the other,
each with its own passes, caps and status file.

Full design, hand steps, restore tests, key rotation and rollback:
[docs/runbooks/photo-offsite.md](../../docs/runbooks/photo-offsite.md) (B2:
[§ B2](../../docs/runbooks/photo-offsite.md#b2)).

## Don't undo these

- **Never run at the same time as `backup-offsite`.** Same host, same
  `/opt/kdk-lab/backup/rclone/rclone.conf`, one Proton session with a single-use refresh
  token. Keep it a separate, later stage.
- **The guard runs before rclone, once for every remote.** A source that is not NFS, lacks
  its marker, is empty or has under 50 % of the last accepted night's files is left out with a
  `- /<source>/**` filter; rclone never deletes excluded destination files. `--max-delete 100`
  caps the rest. Never add `--delete-excluded`.
- **`create_host_path: false` on the Immich binds**, so a missing `/mnt/immich` stops the
  container instead of binding an empty local directory.
- **`- .*.part` stays the first filter**: the insta360-flat worker (02:35) writes
  `.<name>.jpg.part` files and renames them; a half-written still must never ship.
- **`--size-only` for Proton only**: Proton cannot update modification times; originals
  never change in place. B2 keeps rclone's default size + modtime (B2 stores the modtime).
- **B2 never touches `rclone.conf`.** Its remotes are environment, defined in the b2 subshell
  from `/etc/kdk/backup-photos-b2/b2.env` (bound as a **directory** at `/b2`), with
  `--config ""`. A missing file = B2 skipped (`rc=92`), Proton unaffected. Never put the B2
  remotes into the Proton `rclone.conf`, and never give the B2 key `deleteFiles`.
- **Always exits 0.** `backup-verify` reads `/mnt/backup/dkr/.photos-status-{proton,b2}` and
  is the only thing that pages.
- **A crash is not a usage error.** A Go panic exits 2 like a usage error; photos.sh spots the
  `panic:` line in the pass output and records `rc=91` (retried like exit 10, verify WARNs).
  Exit 2 without a panic and exit 7 stop that remote for the night (verify FAILs).

## Test

`sh tests/guard-test.sh` (and `SH="busybox sh" sh tests/guard-test.sh`; CI runs it in the
stack's rclone image, `.github/workflows/backup-photos.yml`): runs `photos.sh`
against local directories with the real rclone and a fake mountinfo: normal run, a delete,
missing mount, empty library, shrink, `PHOTOS_ALLOW_SHRINK`, `--max-delete`, dry run,
copy mode, all sources refused, and (with a fake rclone) a crashed pass that the next pass
resumes, every pass crashing, a real usage error. B2: a real crypt remote over a local
directory with a test-only `b2.env`: both remotes in one run, the DB dumps (and their guard
and rotation), encrypted names, a sha256 restore through `b2-rclone.sh` (from `b2.env` and
from a fake `op`), Proton failing while B2 still runs and the reverse (either order), B2 not
configured, the guard applying to both, and that Proton's rclone flags and environment are
unchanged. Touches no remote, no rclone config and no Backblaze account.
