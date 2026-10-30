# Rendered by Komodo pre_deploy (op inject -f -i .env.tpl -o .env) ONLY on a host
# that has /etc/komodo/op.env. kdk-log-01 has none by design, so on that host
# .env stays empty and CF_API_TOKEN comes from the host file
# /etc/kdk/cloudflare-watch.env (root 600) instead (see compose.yaml + README).
CF_API_TOKEN={{ op://kdk-cluster/cloudflare-monitor-ro/credential }}
CF_ZONE=kmkdp.com
