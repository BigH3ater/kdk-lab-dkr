#!/bin/sh
# rmfakecloud device proxy — restores the self-hosted reMarkable cloud connection
# on every boot. Needed because the Paper Pro wipes /etc (tmpfs overlay) each boot,
# so the CA bundle and the /etc/hosts redirects don't persist. The CA source, proxy
# binary and certs live in /home/root/rmfakecloud (persists). Run by the
# rmfakecloud-proxy.service systemd unit (on the rootfs). Idempotent.
set -e
D=/home/root/rmfakecloud
UPSTREAM="https://remarkable.kmkdp.com"   # the rmfakecloud instance (Traefik -> apps stack)

# NOTE: CA trust + /etc/hosts redirects are applied EARLY by rmfakecloud-catrust.service
# (ordered Before=xochitl.service) — that ordering is what makes pairing work (see that
# script). This boot script (network-gated, so it runs later) re-applies them idempotently
# as a fallback in case catrust did not run, then runs the proxy.

# 1. Trust the rmfakecloud CA. /usr/local/share/ca-certificates/rmfakecloud.crt is on the
#    rootfs (persists); the generated bundle in /etc/ssl is tmpfs, so regenerate it here.
update-ca-certificates 2>/dev/null || true

# 2. Point the reMarkable cloud domains at the local proxy (/etc/hosts is tmpfs).
for d in \
  my.remarkable.com ping.remarkable.com internal.cloud.remarkable.com \
  eu.tectonic.remarkable.com backtrace-proxy.cloud.remarkable.engineering \
  local.appspot.com hwr-production-dot-remarkable-production.appspot.com \
  service-manager-production-dot-remarkable-production.appspot.com \
  document-storage-production-dot-remarkable-production.appspot.com \
  webapp-prod.cloud.remarkable.engineering eu.internal.tctn.cloud.remarkable.com; do
  grep -q "[[:space:]]$d\$" /etc/hosts 2>/dev/null || echo "127.0.0.1 $d" >> /etc/hosts
done

# 3. Run the proxy in the foreground (systemd supervises it). Serve the full chain
#    (proxy.bundle.crt = leaf + CA) so clients that build the chain from what the server
#    presents succeed, not just those with the CA already in their trust store.
exec "$D/rmfakecloud-proxy" -addr :443 -cert "$D/proxy.bundle.crt" -key "$D/proxy.key" "$UPSTREAM"
