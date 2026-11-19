---
title: Laptop rebuild (debian-mbp → mbp-dev, Debian 13)
updated: 2026-11-19
---

# Laptop rebuild

The owner's laptop `debian-mbp` (MacBookPro11,4, Debian 12, kernel 6.1, wifi
`10.1.30.188`) is wiped and reinstalled as **Debian 13 (trixie) amd64**, hostname
**`mbp-dev`**. This page is the plan and the hand procedure; an install script for the
first-boot steps is a later PR, and so is renaming `hosts/debian-mbp/` to
`hosts/mbp-dev/`.

**Related:** [Laptop backup](laptop-backup.md) · [CAD library](cad-library.md) · `hosts/debian-mbp/README.md`

| Decision | Value |
|---|---|
| OS | Debian 13 amd64, official **netinst** image (official images carry the non-free firmware) |
| Hostname / user | `mbp-dev` / `jmack`, uid 1000 (same uid as today, so restored files keep their owner) |
| Disk | guided "use entire disk and set up encrypted LVM" (full-disk LUKS); passphrase in 1Password `kdk-ops/mbp-dev-luks` |
| Desktop | GNOME; SSH server **off** |
| Install network | wired: Apple Thunderbolt 2 Gigabit adapter (`tg3`) |
| Wifi | BCM43602 `[14e4:43ba]`, in-kernel `brcmfmac` + `firmware-brcm80211`. **Not** `broadcom-sta-dkms` / `wl` (the old install had it loaded and unused) |
| Desk network | wired on `10.1.30.0/24` with its own DHCP reservation; wifi as fallback |

## Order

1. Pre-flight gate.
2. LUKS passphrase into 1Password.
3. Install media.
4. Install.
5. First boot: packages, restore, host units.
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
- The Thunderbolt Ethernet adapter and a USB stick (≥ 1 GB, will be erased) are at hand.

## 2. LUKS passphrase (owner, before the install)

Generate it in 1Password while the old laptop still works (or from the 1Password app on
another device); it is typed at every boot:

```sh
env -u OP_SERVICE_ACCOUNT_TOKEN op item create --vault kdk-ops --category password \
  --title mbp-dev-luks --generate-password=letters,digits,32 \
  'notesPlain=LUKS passphrase of the laptop mbp-dev, typed at boot (docs/runbooks/laptop-rebuild.md)' >/dev/null
op read 'op://kdk-ops/mbp-dev-luks/password' | wc -c     # 33 (32 + newline)
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

Plug in the Thunderbolt Ethernet adapter and the stick **before** powering on (a Mac
Thunderbolt adapter may not be seen when plugged in later). Hold **Option (⌥)** at the
chime / power-on and pick **EFI Boot**.

Installer choices:

- Install (graphical or text), language/keyboard as usual.
- Network: the wired interface (`tg3`); hostname `mbp-dev`, domain empty.
- Root password empty (sudo for `jmack`), user `jmack`.
- Partitioning: **Guided - use entire disk and set up encrypted LVM**, whole internal
  disk, all files in one partition; let it overwrite with random data if time allows.
  Passphrase from `kdk-ops/mbp-dev-luks`.
- Software selection: **Debian desktop environment + GNOME + standard system utilities**.
  **SSH server: off.**
- GRUB to the internal disk (EFI).

## 5. First boot

**5a. Packages needed to restore:**

```sh
sudo apt update && sudo apt full-upgrade
sudo apt install restic cifs-utils rsync git iperf3 firmware-brcm80211
lspci -nnk -d 14e4:43ba          # Kernel driver in use: brcmfmac
dpkg -l | grep -E 'broadcom-sta|wl-' || echo "no wl"   # no wl
```

1Password: install the desktop app and CLI from 1Password's apt repository (follow
1Password's "Install on Linux" page for Debian: `1password` and `1password-cli`), sign
in, turn on *Settings → Developer → Integrate with 1Password CLI* and the SSH agent.
On the fresh install bare `op` is the owner's own account until the read-only service
account is set up again.

**5b. Restore:** [Laptop backup → Restore on a fresh machine](laptop-backup.md#restore-on-a-fresh-machine)
(steps 2a–2c there, then the `restic restore` commands). Log out and back in after the
home restore.

**5c. Re-install the host units** from the restored `/bench/kdk-lab-dkr` checkout
(`hosts/debian-mbp/`):

- 3d-prints automount + `cad-sync`: [CAD library](cad-library.md) steps 3a–3d and 3f.
  Skip the pull-back (3e): `~/cad` came back from restic.
- laptop-backup: [Laptop backup](laptop-backup.md) steps 2a–2d and 5. **No `restic
  init`**: the repository exists, and the new install keeps adding to host `mbp-dev`.

## 6. Network

**Wired at the desk.** The owner creates a DHCP reservation for the Thunderbolt adapter
on `10.1.30.0/24` in UniFi (MAC from `ip -br link`). Then check:
`ip -br addr` shows the reserved address on the wired interface.

**Wifi fallback.** Connect to the home SSID in GNOME once, then lock the saved profile to
5 GHz and turn power save off (`2` = disable). The key stays in NetworkManager, never in
git:

```sh
nmcli connection modify "<ssid>" 802-11-wireless.band a 802-11-wireless.powersave 2
nmcli connection up "<ssid>"
```

If `tlp` is installed, it turns wifi power save back on; stop that in a drop-in:

```sh
printf 'WIFI_PWR_ON_AC=off\nWIFI_PWR_ON_BAT=off\n' | sudo install -m 644 /dev/stdin /etc/tlp.d/90-kdk-wifi-powersave.conf
sudo tlp start
```

**Wifi pass mark.** The adapter is Wi-Fi 5, 3 spatial streams, 80 MHz max (ceiling
1300 Mbit/s link rate); the old install sat on 2.4 GHz at 144 Mbit/s. Measure on wifi
only (unplug the Ethernet adapter or `nmcli device disconnect <wired-if>`):

```sh
iw dev                                   # interface name, e.g. wlp3s0
iw dev <if> link                         # freq 5xxx, rx/tx bitrate ... 80MHz
# server on kdk-dkr-01 (own terminal, Ctrl-C when done):
ssh -t kdkadmin@10.1.20.20 'sudo docker run --rm -it --network host networkstatic/iperf3 -s'
iperf3 -c 10.1.20.20 -t 20               # laptop → dkr-01
iperf3 -c 10.1.20.20 -t 20 -R            # dkr-01 → laptop
```

| Check | Pass |
|---|---|
| `iw dev <if> link` | connected on 5 GHz (`freq` 5xxx), 80 MHz |
| link rate (`tx bitrate` / `rx bitrate`) | ≥ 866 Mbit/s |
| `iperf3` both directions | ≥ 400 Mbit/s measured |

If wifi misbehaves, troubleshoot over the wired port (`journalctl -k | grep brcmfmac`,
`nmcli -f all connection show "<ssid>"`).

## Out of scope

USB switch and desk mounting; the access point channel plan.
