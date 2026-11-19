#!/bin/sh
# b2-rclone.sh: rclone against the B2 copy of the photo originals, for restores,
# the restore test and key checks (docs/runbooks/photo-offsite.md#b2). Defines
# the same two remotes photos.sh uses, as environment only (no rclone.conf is
# read or written), then runs `rclone --config "" ARGS...`:
#   b2photoscrypt:photos/immich/...   b2photoscrypt:photos/insta360-flat/...
#   b2photoscrypt:immich-db/immich-YYYY-MM-DD.sql.gz
#   b2photos:<bucket>                 the raw (encrypted) bucket
#
# Credentials, never printed and never written to disk:
#   default  the host file photos.sh reads: $B2_ENV (/b2/b2.env inside the
#            backup-photos image, else /etc/kdk/backup-photos-b2/b2.env)
#   --op     1Password (the laptop: survives the loss of the lab): items
#            kdk-ops/b2-photos-app-key (username = key ID, credential = key,
#            bucket) and kdk-ops/b2-photos-crypt (password, salt), read with
#            $OP (default: env -u OP_SERVICE_ACCOUNT_TOKEN op, the owner's session)
#
#   sh b2-rclone.sh --op lsf b2photoscrypt:
#   sh b2-rclone.sh --op cat b2photoscrypt:photos/insta360-flat/<trip>/<file> | sha256sum
#   sh b2-rclone.sh --op copy b2photoscrypt:photos/immich/library/<user>/<year>/ /tmp/photo-restore/
# Read-only use only: never `sync` FROM here back over a live library.
# Test hook: PHOTOS_B2_UNDERLAY (what the crypt wraps, instead of b2photos:<bucket>).
set -eu
src=file
[ "${1:-}" = --op ] && { src=op; shift; }
if [ -z "${B2_ENV:-}" ]; then
  if [ -r /b2/b2.env ]; then B2_ENV=/b2/b2.env; else B2_ENV=/etc/kdk/backup-photos-b2/b2.env; fi
fi
OP=${OP:-env -u OP_SERVICE_ACCOUNT_TOKEN op}
val() {
  if [ "$src" = op ]; then
    case $1 in
      ACCOUNT) r=op://kdk-ops/b2-photos-app-key/username ;;
      KEY) r=op://kdk-ops/b2-photos-app-key/credential ;;
      BUCKET) r=op://kdk-ops/b2-photos-app-key/bucket ;;
      CRYPT_PASSWORD) r=op://kdk-ops/b2-photos-crypt/password ;;
      CRYPT_SALT) r=op://kdk-ops/b2-photos-crypt/salt ;;
    esac
    $OP read "$r"
  else
    sed -n "s/^PHOTOS_B2_$1=//p" "$B2_ENV" | tail -n 1 | tr -d '\r'
  fi
}
if [ "$src" = file ] && [ ! -r "$B2_ENV" ]; then
  echo "b2-rclone: $B2_ENV not readable (run with sudo / inside the image, or use --op)" >&2; exit 1
fi
for v in ACCOUNT KEY BUCKET CRYPT_PASSWORD CRYPT_SALT; do
  [ -n "$(val "$v")" ] || { echo "b2-rclone: PHOTOS_B2_$v is empty" >&2; exit 1; }
done
RCLONE_CONFIG_B2PHOTOS_TYPE=b2
RCLONE_CONFIG_B2PHOTOS_ACCOUNT=$(val ACCOUNT)
RCLONE_CONFIG_B2PHOTOS_KEY=$(val KEY)
RCLONE_CONFIG_B2PHOTOS_HARD_DELETE=false
RCLONE_CONFIG_B2PHOTOSCRYPT_TYPE=crypt
RCLONE_CONFIG_B2PHOTOSCRYPT_REMOTE=${PHOTOS_B2_UNDERLAY:-b2photos:$(val BUCKET)}
RCLONE_CONFIG_B2PHOTOSCRYPT_FILENAME_ENCRYPTION=standard
RCLONE_CONFIG_B2PHOTOSCRYPT_FILENAME_ENCODING=base32
RCLONE_CONFIG_B2PHOTOSCRYPT_DIRECTORY_NAME_ENCRYPTION=true
RCLONE_CONFIG_B2PHOTOSCRYPT_PASSWORD=$(val CRYPT_PASSWORD | rclone obscure -)
RCLONE_CONFIG_B2PHOTOSCRYPT_PASSWORD2=$(val CRYPT_SALT | rclone obscure -)
export RCLONE_CONFIG_B2PHOTOS_TYPE RCLONE_CONFIG_B2PHOTOS_ACCOUNT RCLONE_CONFIG_B2PHOTOS_KEY \
  RCLONE_CONFIG_B2PHOTOS_HARD_DELETE RCLONE_CONFIG_B2PHOTOSCRYPT_TYPE RCLONE_CONFIG_B2PHOTOSCRYPT_REMOTE \
  RCLONE_CONFIG_B2PHOTOSCRYPT_FILENAME_ENCRYPTION RCLONE_CONFIG_B2PHOTOSCRYPT_FILENAME_ENCODING \
  RCLONE_CONFIG_B2PHOTOSCRYPT_DIRECTORY_NAME_ENCRYPTION RCLONE_CONFIG_B2PHOTOSCRYPT_PASSWORD \
  RCLONE_CONFIG_B2PHOTOSCRYPT_PASSWORD2
exec rclone --config "" "$@"
