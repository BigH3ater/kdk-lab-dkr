# Reusable, pre-authorized, tag:subnet-router auth key (minted via the Tailscale
# admin API). The OAuth client's full-API secret can't be used directly for node
# auth, so a real auth key is required. Node state persists in the volume, so the
# key is only used at first registration.
TS_AUTHKEY="{{ op://kdk-ops/tailscale-subnet-router/authkey }}"
