# op-injected by pre_deploy. Tandoor (recipes) + Postgres. SECRET_KEY + DB password are
# secrets from 1Password; DB name/user are non-secret literals.
SECRET_KEY="{{ op://kdk-cluster/authelia-app-secrets/tandoor_secret_key }}"
POSTGRES_DB="tandoor"
POSTGRES_USER="tandoor"
POSTGRES_PASSWORD="{{ op://kdk-cluster/authelia-app-secrets/tandoor_postgres_password }}"

# Authelia OIDC SSO (django-allauth openid_connect provider "authelia"), per the official
# Authelia<->Tandoor integration guide. SOCIAL_PROVIDERS installs the provider app into
# INSTALLED_APPS -- REQUIRED, else the provider isn't advertised to the login page / headless
# API and no "Sign in with Authelia" button appears. Local login stays enabled;
# SOCIAL_DEFAULT_ACCESS=1 auto-grants access to first-time OIDC users. Callback:
# /accounts/oidc/authelia/login/callback/ (matches the Authelia redirect_uri).
SOCIAL_PROVIDERS=allauth.socialaccount.providers.openid_connect
SOCIAL_DEFAULT_ACCESS=1
SOCIALACCOUNT_PROVIDERS={"openid_connect":{"SCOPE":["openid","profile","email"],"OAUTH_PKCE_ENABLED":true,"APPS":[{"provider_id":"authelia","name":"Authelia","client_id":"tandoor","secret":"{{ op://kdk-cluster/authelia-app-secrets/tandoor_oidc_plaintext }}","settings":{"server_url":"https://sso.kmkdp.com/.well-known/openid-configuration","token_auth_method":"client_secret_basic"}}]}}
