# op-injected by the Komodo pre_deploy (op inject -f -i .env.tpl -o .env), then the
# pre_deploy appends WGER_NGINX_SHA / WGER_POWERSYNC_SHA. compose.yaml hands each value
# only to the services that use it (${VAR} interpolation, no env_file).
#
# Items live in kdk-cluster (container-read vault); create them BEFORE the first deploy
# with the exact commands in docs/runbooks/wger.md step 1. The two Postgres passwords
# are letters+digits only: they sit inside postgres:// URIs.
WGER_SECRET_KEY="{{ op://kdk-cluster/wger-secret-key/password }}"
WGER_POSTGRES_PASSWORD="{{ op://kdk-cluster/wger-postgres/password }}"
WGER_POWERSYNC_STORAGE_PASSWORD="{{ op://kdk-cluster/wger-powersync-storage/password }}"
# RS256 keypair as base64 JWKs (output of `manage.py generate-jwt-keys`); signs the app's
# API JWTs and the PowerSync tokens. One item, two fields.
WGER_JWT_PUBLIC_KEY="{{ op://kdk-cluster/wger-jwt-keys/public_key }}"
WGER_JWT_PRIVATE_KEY="{{ op://kdk-cluster/wger-jwt-keys/private_key }}"
# Break-glass Django `admin` (first-deploy reset in the runbook; daily use is header auth).
WGER_ADMIN_PASSWORD="{{ op://kdk-cluster/wger-admin/password }}"
