# reMarkable Paper Pro — device notes & scripts

Scripts and notes for the tablet (`Codex Linux`, Yocto scarthgap, systemd,
hostname `imx8mm-ferrari`).

## Architecture: lab-side SSH (not Tailscale)

The tablet's kernel has **no `/dev/net/tun`**, so Tailscale can only run in
userspace mode — which does **not** route the device's traffic or set its DNS, so
`xochitl`'s native rmfakecloud sync can't traverse the tailnet. Tailscale on the
tablet bought almost nothing, so it was **removed 2026-09-17** (node deregistered,
daemon stopped/disabled).

Instead the reMarkable pipeline runs **lab-side, opportunistically** — jobs that
act on the tablet whenever it's reachable on home Wi-Fi (it's an
awake-only/opportunistic device anyway):

- **Highlights/annotations → Obsidian:** `remarks` against files pulled over SSH,
  writing Markdown into the headless "Kodiak Codex" vault.
- **Books (send + remove):** `rmapi` against rmfakecloud (internal/LAN).
- **Templates:** `rmtemplate` (Paper Pro template CLI).

rmfakecloud stays **internal (LAN)**; the tablet syncs with it on home Wi-Fi.

## Access

- **LAN (Wi-Fi):** `root@10.1.30.245` (VLAN 30), password in 1Password
  `op://kdk-ops/remarkable-device`. Wi-Fi SSH is enabled by the persistent marker
  `/data/internal/rm_enable_ssh_wifi_marker` + the baked `dropbear-wlan.socket`,
  so it survives reboots.
- **USB:** `root@10.11.99.1`.
- The tablet drops Wi-Fi when asleep — all device I/O is opportunistic.

## Filesystem persistence model (IMPORTANT for any on-device change)

Rootfs `/` is **read-only**, and **`/etc` and `/var/lib` are overlay-on-tmpfs**
(`upperdir=/var/volatile/...`) — so *every* system-config change is **wiped on
reboot**, resetting to the factory image. Only **`/home`** and **`/data`**
persist, and systemd does not scan units from there. This is why a systemd unit
in `/etc` does not survive a reboot; anything that must persist has to live in
`/home` or `/data` and be (re)applied by a lab-side job when the tablet is up.
Firmware updates additionally wipe `/home`-installed binaries' expectations, but
`/home` + `/data` data survive updates.
