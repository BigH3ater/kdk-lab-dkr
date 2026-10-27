# op-injected by pre_deploy (op inject) from vault kdk-cluster, item `jellystat`.
# Two secrets:
#   postgres_password -- the password for the stack's own PostgreSQL (user `jellystat`,
#                        db `jfstat`). Also read by the backup-dkr-01 pg_dump sidecar.
#   jwt_secret        -- random string Jellystat signs its session tokens with.
# Both must be random, 32+ chars. The Jellyfin URL + API key are set in the first-run
# UI, not here. See docs/services/jellystat.md.
POSTGRES_PASSWORD="{{ op://kdk-cluster/jellystat/postgres_password }}"
JWT_SECRET="{{ op://kdk-cluster/jellystat/jwt_secret }}"
