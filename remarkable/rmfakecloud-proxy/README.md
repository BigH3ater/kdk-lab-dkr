# rmfakecloud device proxy + update block (reMarkable Paper Pro)

Onboards the Paper Pro to the self-hosted **rmfakecloud** and blocks OTA updates.
The device wipes `/etc` (tmpfs overlay) every boot, so the cloud connection and
update block must be re-applied from the **rootfs** on each boot.

## How it works
- `rmfakecloud-proxy` (in `/home/root/rmfakecloud/`, persists) runs a TLS reverse
  proxy on **:443** presenting a cert whose SANs cover the reMarkable cloud domains
  (`ca.crt`/`proxy.crt`/`proxy.key` there), forwarding to `https://remarkable.kmkdp.com`
  (the rmfakecloud instance behind Traefik / apps stack).
- `rmfakecloud-boot.sh` (run by the systemd unit each boot): `update-ca-certificates`
  (re-trusts the rmfakecloud CA — source in `/usr/local/share/ca-certificates/`, bundle
  is tmpfs), redirects the reMarkable domains -> `127.0.0.1` in `/etc/hosts`, then execs
  the proxy.
- `rmfakecloud-proxy.service` lives on the **rootfs** at `/usr/lib/systemd/system/` with
  its enable symlink in `/usr/lib/systemd/system/multi-user.target.wants/` (systemd honors
  rootfs `.wants`; `systemctl is-enabled` says "disabled" but it auto-starts — expected).

## Install / restore (after a firmware reset, if one ever happens)
1. Ensure `/home/root/rmfakecloud/` has `rmfakecloud-proxy` + `ca.crt`/`proxy.crt`/`proxy.key`.
2. `scp rmfakecloud-boot.sh root@<tablet>:/home/root/rmfakecloud/` (chmod +x).
3. Install CA:  `mount -o remount,rw / && mkdir -p /usr/local/share/ca-certificates && \`
   `cp /home/root/rmfakecloud/ca.crt /usr/local/share/ca-certificates/rmfakecloud.crt && update-ca-certificates`
4. Install unit + rootfs enable symlink (see rmfakecloud-proxy.service), `mount -o remount,ro /`.
5. `systemctl daemon-reload && systemctl start rmfakecloud-proxy`.
6. Pair: on the tablet, Settings > General > Account > **Set up** -> enter a one-time code
   generated at the rmfakecloud web UI (remarkable.kmkdp.com).

## Block updates forever
- OTA path = **swupdate + memfaultd** (Memfault delivers, swupdate applies). Masked on the
  rootfs: `ln -sf /dev/null /usr/lib/systemd/system/{swupdate.service,swupdate.socket,update-engine.service,memfaultd.service}`.
- `xochitl.conf` `AutoUpdate=false`.
- AdGuard DNS blackholes `device.cloud.remarkable.com` + `device.memfault.com` (dns-reconcile BLOCK set).
- The rootfs masks + DNS block are self-reinforcing (no update can apply to undo them).

Secrets: `proxy.key`/`ca.key` are device-local (NOT in git). Only the boot script + unit are versioned.
