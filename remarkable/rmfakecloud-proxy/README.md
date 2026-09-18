# rmfakecloud device proxy + update block (reMarkable Paper Pro)

Onboards the Paper Pro to the self-hosted **rmfakecloud** and blocks OTA updates.
The device wipes `/etc` (tmpfs overlay) every boot, so the cloud connection and
update block must be re-applied from the **rootfs** on each boot. The rootfs also
differs per **A/B slot** — after an OS switch/downgrade you must reinstall the
rootfs pieces on the new slot (the `/home/root/rmfakecloud/` payload persists, since
`/home` is shared across slots).

## How it works
- `rmfakecloud-proxy` (in `/home/root/rmfakecloud/`, persists) runs a TLS reverse
  proxy on **:443** presenting a cert whose SANs cover the reMarkable cloud domains
  (`proxy.bundle.crt` = leaf + CA; `proxy.key` there), forwarding to
  `https://remarkable.kmkdp.com` (the rmfakecloud instance behind Traefik / apps stack).
- **`rmfakecloud-catrust.{sh,service}` — the critical piece.** `xochitl` (Qt) caches
  the system CA set on its first TLS use and never re-reads it, and it starts EARLY
  (`After=data.mount`, no network dep). If the CA is installed only by the network-gated
  proxy boot script, xochitl caches the *stock* CAs ~6s before ours lands and every
  pairing handshake fails with `QSslError(UnableToGetLocalIssuerCertificate)`. So a
  rootfs oneshot (`/usr/local/sbin/rmfakecloud-catrust.sh`) ordered
  **`Before=xochitl.service`** runs `update-ca-certificates` + the `/etc/hosts`
  redirects EARLY, before xochitl caches trust. It has no `/home` or network dep.
- `rmfakecloud-boot.sh` (run by the proxy unit each boot): re-applies CA + hosts
  idempotently (fallback), then execs the proxy with the full chain.
- Both units live on the **rootfs** at `/usr/lib/systemd/system/` with enable symlinks
  in `/usr/lib/systemd/system/multi-user.target.wants/` (systemd honors rootfs `.wants`;
  `systemctl is-enabled` may say "disabled" but they auto-start — expected).

## Install / restore (fresh slot, firmware reset, or A/B switch)
The `/home/root/rmfakecloud/` payload (certs, `rmfakecloud-proxy` binary, `*-boot.sh`,
`*-catrust.{sh,service}`, `rmfakecloud-proxy.service`) persists on `/home`. If it's
missing, `scp` this dir's files there first. Then, on the tablet:

```sh
D=/home/root/rmfakecloud
mount -o remount,rw /
# 1. CA trust source (rootfs) + regenerate bundle
mkdir -p /usr/local/share/ca-certificates
cp "$D/ca.crt" /usr/local/share/ca-certificates/rmfakecloud.crt
update-ca-certificates
# 2. catrust script (rootfs) — runs before xochitl
cp "$D/rmfakecloud-catrust.sh" /usr/local/sbin/rmfakecloud-catrust.sh
chmod 0755 /usr/local/sbin/rmfakecloud-catrust.sh
# 3. both units + rootfs enable symlinks
cp "$D/rmfakecloud-catrust.service" /usr/lib/systemd/system/
cp "$D/rmfakecloud-proxy.service"   /usr/lib/systemd/system/
mkdir -p /usr/lib/systemd/system/multi-user.target.wants
ln -sf ../rmfakecloud-catrust.service /usr/lib/systemd/system/multi-user.target.wants/rmfakecloud-catrust.service
ln -sf ../rmfakecloud-proxy.service   /usr/lib/systemd/system/multi-user.target.wants/rmfakecloud-proxy.service
sync
mount -o remount,ro /
systemctl daemon-reload
systemctl start rmfakecloud-catrust.service rmfakecloud-proxy.service
```

Then **on first install of a slot, restart xochitl once** so it drops the stale
(stock) CA cache and picks up the rmfakecloud CA — otherwise pairing fails until the
next reboot: `systemctl restart xochitl` (slow; may time out but proceeds).

Verify before pairing (both should be clean):
```sh
systemctl is-active rmfakecloud-catrust rmfakecloud-proxy
echo | openssl s_client -connect 127.0.0.1:443 -servername internal.cloud.remarkable.com 2>/dev/null | grep "Verify return code"   # -> 0 (ok)
```

Pair: on the tablet, **Settings > General > Account > Set up** -> enter a one-time
code generated at the rmfakecloud web UI (`remarkable.kmkdp.com`). Confirm from the
server side with `docker logs rmfakecloud` (`deviceId ... newSync: true`).

## Block updates forever
- OTA path = **swupdate + memfaultd** (Memfault delivers, swupdate applies). Mask on the
  rootfs (remount rw first): `ln -sf /dev/null /usr/lib/systemd/system/{swupdate.service,swupdate.socket,update-engine.service,memfaultd.service}`.
- `xochitl.conf` `AutoUpdate=false` (in `/home`, persists across slots).
- AdGuard DNS blackholes `device.cloud.remarkable.com` + `device.memfault.com` (dns-reconcile BLOCK set) — network-side, so it holds on any slot even before the masks are re-applied.

Secrets: `proxy.key`/`ca.key` are device-local (NOT in git). Only the boot/catrust
scripts + units are versioned.
