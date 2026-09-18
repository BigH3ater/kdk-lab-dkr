#!/bin/sh
# rmfakecloud CA trust + reMarkable host redirects — applied on EVERY boot BEFORE
# xochitl starts (via rmfakecloud-catrust.service, ordered Before=xochitl.service).
#
# Why this exists (the boot race): /etc is a tmpfs overlay wiped every boot, so the CA
# bundle and /etc/hosts must be reapplied each time. xochitl caches the system CA set on
# its first TLS use and never re-reads it. xochitl starts early (After=data.mount, NO
# network dep) while the proxy boot script only runs After=network-online.target — so
# xochitl loaded the STOCK CA set ~6s before our CA was installed, and every pairing
# handshake failed with QSslError(UnableToGetLocalIssuerCertificate). Running the CA +
# hosts step here, ordered before xochitl, closes that window.
#
# Lives on the rootfs (/usr/local/sbin) so it has no /home or network dependency and is
# available the instant local-fs.target is up. Self-contained + idempotent.
set -e

# 1. Trust the rmfakecloud CA. The source cert /usr/local/share/ca-certificates/
#    rmfakecloud.crt is on the rootfs (persists); the generated /etc/ssl bundle is tmpfs
#    (wiped each boot) so regenerate it. /usr/lib/ssl-3/certs -> /etc/ssl/certs, which is
#    what xochitl's Qt/OpenSSL reads by default.
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
