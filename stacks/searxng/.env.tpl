# op-injected by the Komodo pre_deploy (op inject -f -i .env.tpl -o .env), then the
# pre_deploy appends SEARXNG_CONFIG_SHA. compose.yaml hands the value to the searxng
# service by ${VAR} interpolation (no env_file).
#
# The item lives in kdk-cluster (container-read vault); create it BEFORE the first
# deploy with the command in docs/runbooks/searxng.md step 1 (openssl rand -hex 32).
# SEARXNG_SECRET overrides server.secret_key (signs the Flask session cookie and the
# image-proxy URLs); SearXNG refuses to start on the upstream placeholder value.
SEARXNG_SECRET="{{ op://kdk-cluster/searxng/secret }}"
