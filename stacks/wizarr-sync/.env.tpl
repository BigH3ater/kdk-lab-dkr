# op-injected by pre_deploy (op inject -i .env.tpl -o .env). Two secrets:
#   LLDAP admin password  -> GraphQL (add invited LDAP users to jellyfin-users)
#   Jellyfin API key      -> set the LDAP auth provider on those Jellyfin accounts
# Seerr's API key is NOT here: it is read at runtime from the mounted (ro)
# /mnt/appdata/jellyseerr/settings.json (main.apiKey). Pushover creds come from
# the mounted /etc/kdk/pushover.env. See docs/services/wizarr.md.
#
# The Jellyfin key is a DEDICATED per-consumer key (Jellyfin App=wizarr-sync), not
# the old shared op://kdk-cluster/jellyfin/api-key. The owner mints it (one op
# helper, run with their personal op session) into 1P item jellyfin-api-wizarr-sync
# BEFORE this merges; the old shared key is revoked only after wizarr-sync and the
# Homepage widget are verified on their new keys. See the PR for the helper + steps.
LLDAP_ADMIN_PASSWORD="{{ op://kdk-cluster/lldap-admin/password }}"
JELLYFIN_API_KEY="{{ op://kdk-cluster/jellyfin-api-wizarr-sync/api-key }}"
