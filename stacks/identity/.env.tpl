# lldap directory admin: lldap itself + break-glass only (Authelia no longer binds as admin).
LLDAP_ADMIN_PASSWORD="{{ op://kdk-cluster/lldap-admin/password }}"
# Authelia's LDAP bind uid=authelia-bind (lldap_password_manager only). Item must exist before merge.
LLDAP_AUTHELIA_BIND_PASSWORD="{{ op://kdk-cluster/lldap-authelia-bind/password }}"
LLDAP_JWT_SECRET="{{ op://kdk-cluster/lldap-app-secrets/jwt_secret }}"
LLDAP_KEY_SEED="{{ op://kdk-cluster/lldap-app-secrets/key_seed }}"
AUTHELIA_POSTGRES_PASSWORD="{{ op://kdk-cluster/authelia-postgres/password }}"
# Redis requirepass (alphanumeric only; it is written into a quoted redis.conf string). Item must exist before merge.
AUTHELIA_REDIS_PASSWORD="{{ op://kdk-cluster/authelia-redis/password }}"
AUTHELIA_JWT_SECRET="{{ op://kdk-cluster/authelia-app-secrets/reset_password_jwt_secret }}"
AUTHELIA_SESSION_SECRET="{{ op://kdk-cluster/authelia-app-secrets/session_encryption_key }}"
AUTHELIA_STORAGE_ENCRYPTION_KEY="{{ op://kdk-cluster/authelia-app-secrets/storage_encryption_key }}"
AUTHELIA_SMTP_PASSWORD="{{ op://kdk-cluster/proton-bridge-smtp/password }}"
OIDC_HMAC_SECRET="{{ op://kdk-cluster/authelia-app-secrets/oidc_hmac_secret }}"
