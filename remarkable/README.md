# reMarkable Paper Pro — device notes & scripts

Scripts and notes for the tablet (`Codex Linux` 5.7.126, Yocto scarthgap,
build 2026-06-12, kernel `6.12.49`, systemd, hostname `imx8mm-ferrari`).

## Architecture: Tailscale (kernel mode) + opportunistic LAN SSH

The tablet **is** on the tailnet. Tailscale runs in **kernel mode** via the
**Vellum** homebrew framework (`/home/root/.vellum/bin/tailscale[d]`), as node
`remarkable-pp` (`100.103.233.103`). The kernel **does** have `/dev/net/tun`
(char device `10, 200`, world-rw) and the `tun.ko` module loads
(`modprobe tun` → `rc=0`; `/proc/misc` lists `200 tun`), so kernel mode works and
the device's **own** traffic is routed — not just inbound connections (which is
all userspace mode would have given).

> **History:** userspace-only Tailscale was **removed 2026-09-17** under the old
> firmware (which lacked a usable `/dev/net/tun`); it was **re-added via Vellum
> and confirmed running in kernel mode on 2026-10-30** (live SSH probe).

Even so, **LAN `root@10.1.30.245` (VLAN 30) stays the primary path**: the tablet
is an awake-only/opportunistic device and drops Wi-Fi (and the tailnet) when it
sleeps, so device I/O still happens only when the tablet is awake. The tablet
**filters ICMP**, so probe reachability with **SSH, not `ping`** (`ping` reports
100% loss even while SSH is up).

The reMarkable pipeline runs **lab-side, opportunistically** — jobs that
act on the tablet whenever it's reachable:

- **Highlights/annotations → Obsidian:** `remarks` against files pulled over SSH,
  writing Markdown into the headless "Kodiak Codex" vault.
- **Readwise articles → KOReader:** `readwise-koreader` (revived 2026-10-30) mirrors
  Reader docs tagged `remarkable` as EPUBs into `/home/root/books/Readwise`; highlights
  ride back out via `koreader-highlights`. It SSH-probes the tailnet node
  (`remarkable-pp` / `100.103.233.103`) first, then the LAN (`10.1.30.245`) — so delivery
  works from anywhere, not only on home Wi-Fi. See `docs/services/readwise-koreader.md`.
- **Books (send + remove):** `rmapi` against rmfakecloud (internal/LAN).
- **Templates:** `rmtemplate` (Paper Pro template CLI).

rmfakecloud stays **internal (LAN)**; the tablet syncs with it on home Wi-Fi.

## Access

- **LAN (Wi-Fi):** `root@10.1.30.245` (VLAN 30), password in 1Password
  `op://kdk-cluster/remarkable-device`. Wi-Fi SSH is enabled by the persistent marker
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
