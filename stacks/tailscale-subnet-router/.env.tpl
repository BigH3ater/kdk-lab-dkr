# Tailscale OAuth client secret used as the subnet router's auth key. Combined
# with --advertise-tags=tag:subnet-router it registers as a tagged node.
TS_AUTHKEY="{{ op://kdk-ops/tailscale-admin-api/api_token }}"
