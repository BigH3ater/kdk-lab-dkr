# op-injected by pre_deploy. Tandoor (recipes) + Postgres. SECRET_KEY + DB password are
# secrets from 1Password; DB name/user are non-secret literals.
SECRET_KEY="{{ op://kdk-cluster/authelia-app-secrets/tandoor_secret_key }}"
POSTGRES_DB="tandoor"
POSTGRES_USER="tandoor"
POSTGRES_PASSWORD="{{ op://kdk-cluster/authelia-app-secrets/tandoor_postgres_password }}"
