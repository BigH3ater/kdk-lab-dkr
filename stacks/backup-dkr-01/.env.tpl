# op-injected by pre_deploy (op inject). The pgdump-jellystat sidecar needs the
# Jellystat PostgreSQL password -- the SAME secret the jellystat stack uses
# (vault kdk-cluster, item `jellystat`, field postgres_password).
JELLYSTAT_POSTGRES_PASSWORD="{{ op://kdk-cluster/jellystat/postgres_password }}"
