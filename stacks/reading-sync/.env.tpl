# Rendered by Komodo pre_deploy: op inject -f -i .env.tpl -o .env
# Only kdk-cluster vault refs (the vault 1Password Connect may read). Never write an
# op URI in a comment: op inject expands every one it sees, comments included.
# compose.yaml hands each service only its own values (${VAR} interpolation).
# Values are letters/digits (passwords) or ABS JWTs: no $, comma or colon.
OPDS_USERS="jmack:{{ op://kdk-cluster/abs-opds-abs-api-key/password }}:{{ op://kdk-cluster/abs-opds-jmack/password }}"
KOSYNC_HELPER_ABS_API_KEY="{{ op://kdk-cluster/kosync-helper-abs-api-key/password }}"
BOOKBRIDGE_ABS_API_KEY="{{ op://kdk-cluster/bookbridge-abs-api-key/password }}"
BOOKBRIDGE_SECRET_KEY="{{ op://kdk-cluster/bookbridge-secret-key/password }}"
BOOKBRIDGE_KOSYNC_PASSWORD="{{ op://kdk-cluster/bookbridge-kosync-jmack/password }}"
