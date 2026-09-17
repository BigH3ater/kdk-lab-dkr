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

## Filesystem persistence model (IMPORTANT)

On the Paper Pro (Codex Linux) the rootfs is **read-only** and **`/etc` AND
`/var/lib` are overlay-on-tmpfs** (`upperdir=/var/volatile/...`) — so *every*
system-config change is **wiped on reboot**, resetting to the factory image.
Only **`/home`** and **`/data`** persist. Verified 2026-09-17 by a reboot test.

Consequences:
- A systemd unit in `/etc/systemd/system` does **NOT survive a reboot** — systemd
  does not scan units from `/home`/`/data`, so there is no drop-a-unit persistence.
- SSH-over-Wi-Fi *does* survive: `dropbear-wlan` is baked into the RO image and
  gated by the persistent `/data/internal/rm_enable_ssh_wifi_marker` (a factory
  hook, the only supported extension point).

## `install-tailscale-persistence.sh`

Installs the tailscaled systemd unit and starts it. **This starts tailscaled now
but does NOT survive a reboot** (the unit is in ephemeral `/etc`) — it must be
re-applied whenever the tablet comes back up. Binaries + node state live in
`/home/root` (`tailscale_*_arm64/`, `tailscaled.state`) and DO persist, and prefs
(accept-routes, ssh, `tag:remarkable`) are in the state file, so re-running only
re-creates the unit — no `tailscale up` / re-auth. A copy of this script lives at
`/home/root/` (persistent).

**Real persistence** (survive reboot) needs a lab-side reconciler: a scheduled job
that SSHes to the tablet whenever it's reachable on Wi-Fi and re-applies this
script if tailscaled isn't running. That fits the device's opportunistic nature
(it's only online when awake anyway). TBD — see the session notes.

Run on the tablet: `sh /home/root/install-tailscale-persistence.sh`
