# Rendered by Komodo pre_deploy: op inject -f -i .env.tpl -o .env
# Only kdk-cluster vault refs (the vault containers may read). Never write an
# op URI in a comment: op inject expands every one it sees, comments included.
APP_SECRET_KEY="{{ op://kdk-cluster/template-app/secret_key }}"
POSTGRES_PASSWORD="{{ op://kdk-cluster/template-postgres/password }}"
