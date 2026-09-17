# reMarkable Paper Pro — device scripts

Scripts that run **on the tablet** (not in Komodo). The reMarkable is `Codex
Linux` (Yocto scarthgap, systemd), hostname `imx8mm-ferrari`, tag `tag:remarkable`.

## Access

- **Tailnet:** `remarkable` = `100.78.81.105` (userspace `tailscaled`). Tailscale
  SSH to the tablet is **disabled** (reMarkable has no `/var` for SSH host keys),
  so manage it over the **LAN via dropbear**, not Tailscale SSH.
- **LAN (Wi-Fi):** `root@10.1.30.245` (VLAN 30), password in 1Password
  `op://kdk-ops/remarkable-device`. Find the current Wi-Fi IP if it drifts:
  `docker exec tailscale-subnet-router tailscale ping remarkable` (shows `via <ip>`).
- **USB:** `root@10.11.99.1`.
- The tablet drops Wi-Fi/Tailscale when asleep — all device I/O is opportunistic.

## `install-tailscale-persistence.sh`

Makes `tailscaled` survive a reboot. It was started by hand (`nohup`) and did
**not** come back after a reboot, silently breaking remote sync. This installs a
systemd unit (`/etc/systemd/system/tailscaled.service`, `Restart=always`,
`enabled`) running the userspace daemon with the persisted node state.

Persistence model:
- Binaries + state under `/home/root` (`tailscale_*_arm64/`, `tailscaled.state`)
  **survive firmware updates**.
- The systemd unit under `/etc` survives reboots but is **wiped by a firmware
  update** → **re-run this script after every firmware update** (it's idempotent).

Run on the tablet: `sh /home/root/install-tailscale-persistence.sh`
(a copy lives at `/home/root/` so it's available after a FW update). Node prefs
(accept-routes, ssh, `tag:remarkable`) are persisted in `tailscaled.state`, so no
`tailscale up` / re-auth is needed.
