# op-injected by pre_deploy (op inject -i .env.tpl -o .env). Two secrets:
#   LLDAP admin password  -> GraphQL (add invited LDAP users to jellyfin-users)
#   Jellyfin API key      -> set the LDAP auth provider on those Jellyfin accounts
# Seerr's API key is NOT here: it is read at runtime from the mounted (ro)
# /mnt/appdata/jellyseerr/settings.json (main.apiKey). Pushover creds come from
# the mounted /etc/kdk/pushover.env. See docs/services/wizarr.md.
LLDAP_ADMIN_PASSWORD="{{ op://kdk-cluster/lldap-admin/password }}"
JELLYFIN_API_KEY="{{ op://kdk-cluster/jellyfin/api-key }}"
