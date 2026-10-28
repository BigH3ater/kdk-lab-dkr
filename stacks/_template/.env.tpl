# Rendered by Komodo pre_deploy: op inject -f -i .env.tpl -o .env
# Only op://kdk-cluster/... refs (the vault containers may read).
APP_SECRET_KEY="{{ op://kdk-cluster/template-app/secret_key }}"
POSTGRES_PASSWORD="{{ op://kdk-cluster/template-postgres/password }}"
