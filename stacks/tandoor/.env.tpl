# op-injected by pre_deploy. Tandoor (recipes) + Postgres. SECRET_KEY + DB password are
# secrets from 1Password; DB name/user are non-secret literals.
SECRET_KEY="{{ op://kdk-cluster/authelia-app-secrets/tandoor_secret_key }}"
POSTGRES_DB="tandoor"
POSTGRES_USER="tandoor"
POSTGRES_PASSWORD="{{ op://kdk-cluster/authelia-app-secrets/tandoor_postgres_password }}"

# Authelia OIDC SSO (django-allauth openid_connect provider "authelia"). Adds a
# "Sign in with Authelia" button; local login stays enabled. SOCIAL_DEFAULT_ACCESS=1
# auto-grants access to first-time OIDC users. allauth callback:
# /accounts/oidc/authelia/login/callback/ (matches the Authelia redirect_uri).
SOCIAL_DEFAULT_ACCESS=1
SOCIALACCOUNT_PROVIDERS={"openid_connect":{"APPS":[{"provider_id":"authelia","name":"Authelia","client_id":"tandoor","secret":"{{ op://kdk-cluster/authelia-app-secrets/tandoor_oidc_plaintext }}","settings":{"server_url":"https://sso.kmkdp.com/.well-known/openid-configuration"}}]}}
