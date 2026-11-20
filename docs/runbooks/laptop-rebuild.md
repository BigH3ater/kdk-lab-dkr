---
title: Laptop rebuild (debian-mbp → mbp-dev, Debian 13)
updated: 2026-11-20
---

# Laptop rebuild

**Done 2026-11-19.** The owner's laptop `debian-mbp` (MacBookPro11,4, Debian 12, kernel
6.1, wifi `10.1.30.188`) was wiped and reinstalled as **Debian 13 (trixie) amd64**,
kernel 6.12, GNOME 48 on Wayland, full-disk LUKS, hostname **`mbp-dev`** (same wifi
address). This page is the procedure as it was actually run; the first-boot setup is
now `hosts/mbp-dev/install.sh`.

**Related:** [Laptop backup](laptop-backup.md) · [CAD library](cad-library.md) · `hosts/mbp-dev/README.md`

| Decision | Value |
|---|---|
| OS | Debian 13 amd64, official **netinst** image (official images carry the non-free firmware) |
| Hostname / user | `mbp-dev` / `jmack`, uid 1000 (same uid as today, so restored files keep their owner) |
| Disk | guided "use entire disk and set up encrypted LVM" (full-disk LUKS); passphrase in 1Password `kdk-ops/mbp-dev-luks` |
| Desktop | GNOME; SSH server **off** |
| Install network | **wifi** (planned wired; the white Apple Thunderbolt Gigabit adapter was not detected on three boots: nothing on the Thunderbolt bus, no `tg3` line in dmesg; treated as dead, a USB 3.0 Gigabit adapter is the planned replacement) |
| Wifi | BCM43602 `[14e4:43ba]`, in-kernel `brcmfmac` + `firmware-brcm80211`. **Not** `broadcom-sta-dkms` / `wl` (the old install had it loaded and unused) |
| Desk network | planned: wired on `10.1.30.0/24` with its own DHCP reservation once the USB adapter exists; wifi until then |

## Order

1. Pre-flight gate.
2. LUKS passphrase into 1Password.
3. Install media.
4. Install.
5. First boot: 1Password, `install.sh`, restore.
6. Network: wired reservation, wifi settings, wifi pass mark.

## 1. Pre-flight gate

All of these, or no wipe:

- [Laptop backup](laptop-backup.md) step 3 (`pre-wipe` snapshot) and step 4 (test
  restore) **passed**. Run step 3 once more right before the wipe if anything changed.
- This page and the laptop-backup page open on another device (GitHub
  `BigH3ater/kdk-lab-dkr` → `docs/runbooks/`, or the docs site): this machine will be blank.
- 1Password works on that other device (phone or another computer): the install and the
  restore need `kdk-ops/mbp-dev-luks`, `kdk-ops/kdk-nas-01-laptop-backup-smb` and
  `kdk-ops/mbp-dev-restic-repo`.
- A USB stick (≥ 1 GB, will be erased) is at hand, and a wired adapter if one works
  (the Thunderbolt one did not, see the table).

## 2. LUKS passphrase (owner, before the install)

Generate it in 1Password while the old laptop still works (or from the 1Password app on
another device); it is typed at every boot. As built: an **18-character** passphrase in
`kdk-ops/mbp-dev-luks`:

```sh
env -u OP_SERVICE_ACCOUNT_TOKEN op item create --vault kdk-ops --category password \
  --title mbp-dev-luks --generate-password=letters,digits,18 \
  'notesPlain=LUKS passphrase of the laptop mbp-dev, typed at boot (docs/runbooks/laptop-rebuild.md)' >/dev/null
op read 'op://kdk-ops/mbp-dev-luks/password' | wc -c     # 19 (18 + newline)
```

## 3. Install media

**Already done 2026-11-19:** the 13.7.0 netinst image is downloaded and verified
(signature + checksum) at `/home/jmack/Downloads/debian-13.7.0/`. Downloads is
**excluded** from the backup, so write the USB stick from it **before the wipe** (the
`dd` step below); the download and verify steps are only for a fresh copy.

From https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/ download
`debian-13.*-amd64-netinst.iso`, `SHA256SUMS` and `SHA256SUMS.sign`. Verify the
signature, then the checksum:

```sh
sudo apt install debian-keyring        # holds the Debian CD signing key (debian-role-keys.gpg)
gpg --no-default-keyring --keyring /usr/share/keyrings/debian-role-keys.gpg --verify SHA256SUMS.sign SHA256SUMS
# Good signature from "Debian CD signing key <debian-cd@lists.debian.org>";
# compare the primary key fingerprint with https://www.debian.org/CD/verify
sha256sum -c --ignore-missing SHA256SUMS          # debian-13.*-amd64-netinst.iso: OK
```

Write it to the stick. **This erases the stick.** Pick the device by size and model, never
guess:

```sh
lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINTS           # the stick: TRAN usb, e.g. sdb
sudo umount /dev/sdX?* 2>/dev/null
sudo dd if=debian-13.*-amd64-netinst.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

(`sudo cp debian-13.*-amd64-netinst.iso /dev/sdX && sync` works too.)

## 4. Install

Plug in the stick (and a wired adapter, if any) **before** powering on. Hold
**Option (⌥)** at the chime / power-on and pick **EFI Boot**.

Installer choices:

- Install (graphical or text), language/keyboard as usual.
- Network: wifi (the netinst image carries the `brcmfmac` firmware; as run 2026-11-19),
  or the wired interface if one is detected; hostname `mbp-dev`, domain empty.
- Root password empty (sudo for `jmack`), user `jmack`.
- Partitioning: **Guided - use entire disk and set up encrypted LVM**, whole internal
  disk, all files in one partition; let it overwrite with random data if time allows.
  Passphrase from `kdk-ops/mbp-dev-luks`.
- Software selection: **Debian desktop environment + GNOME + standard system utilities**.
  **SSH server: off.**
- GRUB to the internal disk (EFI).

## 5. First boot

**5a. 1Password first** (by hand: `install.sh` section `e` needs it). Install the
desktop app and CLI from 1Password's apt repository (1Password's "Install on Linux" page
for Debian, `1password` and `1password-cli`; `install.sh` section `b` adds the same
repository), sign in, turn on *Settings → Developer → Integrate with 1Password CLI* and
the SSH agent. On the fresh install bare `op` is the owner's own account until the
read-only service account is set up again. Check: `op vault list` shows `kdk-ops`.

**5b. Restore** (by hand: the repo with `install.sh` is one of the restored folders).
`sudo apt update && sudo apt full-upgrade && sudo apt install restic cifs-utils`, then
[Laptop backup → Restore on a fresh machine](laptop-backup.md#restore-on-a-fresh-machine):
the two files from 1Password (laptop-backup step 2b), the share mounted by hand, the
selective restore straight into place. Log out and back in after it.

**5c. `install.sh`** replaces the old hand steps (packages, apt repositories, power,
wifi profile, host units and their credential files):

```sh
bash /bench/kdk-lab-dkr/hosts/mbp-dev/install.sh          # all sections a-g; or e.g. `... install.sh e`
lspci -nnk -d 14e4:43ba          # Kernel driver in use: brcmfmac
dpkg -l | grep -E 'broadcom-sta|wl-' || echo "no wl"   # no wl
systemctl list-timers laptop-backup.timer; systemctl --user list-timers cad-sync.timer
```

Safe to re-run. It never runs `tailscale up` (do that by hand) and never touches LUKS.
The restic repository already exists: **no `restic init`**; the new install keeps adding
to host `mbp-dev`. Skip the CAD pull-back (cad-library 3e): `~/cad` came back from restic.

**5d. Not restored, by hand:** the list in
[Laptop backup → Does NOT come back](laptop-backup.md#restore-on-a-fresh-machine)
(GNOME keyring / `gh auth login`, 1Password SSH agent, `.venv` folders, uv tools,
SolidCraft file types, Claude Code). Node.js is `install.sh` section `g`, see "Found on trixie".

## 6. Network

**Wired at the desk** (when the USB 3.0 Gigabit adapter exists): the owner creates a
DHCP reservation for it on `10.1.30.0/24` in UniFi (MAC from `ip -br link`). Then check:
`ip -br addr` shows the reserved address on the wired interface.

**Wifi.** Applied by `install.sh` sections `c` and `d` on the active wifi connection
(no SSID or key in git): `802-11-wireless.band a` (5 GHz only),
`802-11-wireless.powersave 2` (off), and the tlp drop-in
`/etc/tlp.d/90-kdk-wifi-powersave.conf` (`WIFI_PWR_ON_AC=off`, `WIFI_PWR_ON_BAT=off`) so
tlp does not turn power save back on. By hand:

```sh
nmcli connection modify "<ssid>" 802-11-wireless.band a 802-11-wireless.powersave 2
nmcli connection up "<ssid>"
```

**Wifi pass mark.** The adapter is Wi-Fi 5, 3 spatial streams, 80 MHz max (ceiling
1300 Mbit/s link rate). Measure on wifi only, against a one-shot server on the NAS
(TrueNAS has `iperf3` built in, so nothing to run on kdk-dkr-01):

```sh
iw dev                                   # interface name, e.g. wlp3s0
iw dev <if> link                         # freq 5xxx, rx/tx bitrate ... 80MHz
ssh kdkadmin@10.1.3.5 'iperf3 -s -1 -D'  # one test, then the server exits
iperf3 -c 10.1.3.5 -t 15                 # laptop → NAS
ssh kdkadmin@10.1.3.5 'iperf3 -s -1 -D'
iperf3 -c 10.1.3.5 -t 15 -R              # NAS → laptop
```

| Check | Pass | Measured 2026-11-20 |
|---|---|---|
| `iw dev <if> link` | 5 GHz (`freq` 5xxx), 80 MHz | channel 40, 80 MHz, about -38 dBm |
| link rate (`tx bitrate` / `rx bitrate`) | ≥ 866 Mbit/s | 520–866 Mbit/s |
| `iperf3` up / down | ≥ 400 Mbit/s | 285–341 up, 325–367 down: **not met** |

The old install for comparison: 2.4 GHz, 144 Mbit/s link. The cause of the shortfall is
open; suspects: two access points sharing the 36–48 channel block, and the driver's rate
selection. If wifi misbehaves: `journalctl -k | grep brcmfmac`,
`nmcli -f all connection show "<ssid>"`.

## Found on trixie

- **WezTerm** stable 20240203 dies on native Wayland under GNOME 48 with a `wl_surface`
  protocol error. Fix: `config.enable_wayland = false` in `~/.config/wezterm/wezterm.lua`,
  or the `wezterm-nightly` package.
- **Node.js:** Node 24 LTS per-user under `~/.local/opt/node`, linked into
  `~/.local/bin` (`install.sh` section `g`). Not Node 26: the pro-workflow plugin's
  `better-sqlite3` has no prebuilt binary for it. After any Node change run
  `npm rebuild better-sqlite3` in `~/.claude/plugins/cache/pro-workflow/pro-workflow/<version>/`.
- **Bambu Studio** is the system Flatpak `com.bambulab.BambuStudio` (`install.sh`
  section `b`, settings in `~/.var/app/com.bambulab.BambuStudio`), started by the
  `~/.local/bin/bambu-studio` wrapper. The restored `~/.local/opt/bambu-studio-*` copy
  and its `BambuStudio.desktop` are leftovers and were removed from the launcher.
- **Battery:** the kernel `capacity` value is charge ÷ **design** capacity (78 % when
  full, at 81 % health); UPower/GNOME show charge ÷ current full capacity. Not a fault.
  This model has no charge-threshold control.

## Out of scope

USB switch and desk mounting; the access point channel plan.
