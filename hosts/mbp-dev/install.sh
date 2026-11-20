#!/usr/bin/env bash
# install.sh -- post-install setup of the laptop mbp-dev (Debian 13 trixie,
# MacBookPro11,4). Replaces the first-boot hand steps of
# docs/runbooks/laptop-rebuild.md section 5. Run as jmack from a checkout of
# this repo; it calls sudo. Safe to re-run: files are only rewritten when their
# content changed, credential files only when missing or empty.
#
# Sections (default: all, in order; or name some, e.g. `install.sh e`):
#   a  base packages
#   b  apt repositories + packages: Docker Engine, Tailscale, VS Code, Signal,
#      WezTerm, 1Password (desktop + CLI); Bambu Studio from Flathub
#   c  tlp (+ wifi power save off), thermald, mbpfan
#   d  wifi profile settings on the ACTIVE wifi connection (5 GHz, power save off)
#   e  host units: 3d-prints + laptop-backup mounts/automounts, laptop-backup
#      script/excludes/service/timer, cad-sync script + user timer, and the
#      credential files from 1Password
#   f  GNOME: battery percentage in the top bar
#   g  Node.js 24 LTS per-user in ~/.local/opt/node (no sudo)
#
# Before section e: sign in to the 1Password desktop app and turn on
# Settings -> Developer -> Integrate with 1Password CLI; e stops with a message
# when `op read` cannot read the kdk-ops vault.
#
# Never runs `tailscale up` (the owner does, interactively), never touches LUKS,
# never prints a secret.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ME=jmack
ARCH=$(dpkg --print-architecture)
TMPD=$(mktemp -d)
trap 'rm -rf "$TMPD"' EXIT

say() { printf '\n== %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

# put MODE DEST: root-owned file from stdin, written only when it changed.
put() {
  local mode=$1 dest=$2 tmp
  tmp=$(mktemp -p "$TMPD")
  cat >"$tmp"
  [ -s "$tmp" ] || die "no content for $dest (download failed?)"
  if ! sudo cmp -s "$tmp" "$dest" 2>/dev/null; then
    sudo install -D -m "$mode" -o root -g root "$tmp" "$dest"
    echo "  wrote $dest"
  fi
}

# key URL DEST [dearmor]: fetch an apt signing key (armored -> binary when asked).
key() {
  local url=$1 dest=$2 how=${3:-}
  if [ "$how" = dearmor ]; then
    curl -fsSL "$url" | gpg --dearmor | put 0644 "$dest"
  else
    curl -fsSL "$url" | put 0644 "$dest"
  fi
}

apt_install() { sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"; }

# ---------------------------------------------------------------------------
sec_a() {
  say "a. base packages"
  sudo apt-get update
  # ca-certificates, curl, gnupg: needed by section b.
  apt_install restic cifs-utils rsync git curl ca-certificates gnupg iperf3 \
    firmware-brcm80211 tmux build-essential smbclient thermald mbpfan
}

# ---------------------------------------------------------------------------
sec_b() {
  say "b. apt repositories: Docker, Tailscale, VS Code, Signal, WezTerm, 1Password"
  command -v curl >/dev/null && command -v gpg >/dev/null || die "curl/gpg missing: run section a first"

  # Docker Engine (not Docker Desktop).
  key https://download.docker.com/linux/debian/gpg /etc/apt/keyrings/docker.asc
  echo "deb [arch=$ARCH signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian trixie stable" \
    | put 0644 /etc/apt/sources.list.d/docker.list

  # Tailscale: key is already binary (noarmor).
  key https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list \
    | put 0644 /etc/apt/sources.list.d/tailscale.list

  # VS Code. Tell the package not to add a second copy of the repository.
  key https://packages.microsoft.com/keys/microsoft.asc /usr/share/keyrings/microsoft.gpg dearmor
  echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/repos/code stable main" \
    | put 0644 /etc/apt/sources.list.d/vscode.list
  echo "code code/add-microsoft-repo boolean false" | sudo debconf-set-selections

  # Signal (one "xenial" suite for every Debian/Ubuntu release).
  key https://updates.signal.org/desktop/apt/keys.asc /usr/share/keyrings/signal-desktop-keyring.gpg dearmor
  echo "deb [arch=amd64 signed-by=/usr/share/keyrings/signal-desktop-keyring.gpg] https://updates.signal.org/desktop/apt xenial main" \
    | put 0644 /etc/apt/sources.list.d/signal-desktop.list

  # WezTerm. Stable 20240203 dies on native Wayland under GNOME 48: set
  # config.enable_wayland = false in ~/.config/wezterm/wezterm.lua (restored
  # from the backup), or install wezterm-nightly instead.
  key https://apt.fury.io/wez/gpg.key /usr/share/keyrings/wezterm-fury.gpg dearmor
  echo "deb [signed-by=/usr/share/keyrings/wezterm-fury.gpg] https://apt.fury.io/wez/ * *" \
    | put 0644 /etc/apt/sources.list.d/wezterm.list

  # 1Password: repository + debsig policy (1Password's "Install on Linux" steps).
  key https://downloads.1password.com/linux/keys/1password.asc /usr/share/keyrings/1password-archive-keyring.gpg dearmor
  echo "deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main" \
    | put 0644 /etc/apt/sources.list.d/1password.list
  curl -fsSL https://downloads.1password.com/linux/debian/debsig/1password.pol \
    | put 0644 /etc/debsig/policies/AC2D62742012EA22/1password.pol
  key https://downloads.1password.com/linux/keys/1password.asc /usr/share/debsig/keyrings/AC2D62742012EA22/debsig.gpg dearmor

  sudo apt-get update
  apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
    tailscale code signal-desktop wezterm 1password 1password-cli
  if ! id -nG "$ME" | tr ' ' '\n' | grep -qx docker; then
    sudo usermod -aG docker "$ME"
    echo "  $ME added to group docker (log out and back in)"
  fi
  echo "  tailscale installed, not joined: run 'sudo tailscale up' yourself"

  # Bambu Studio is the system Flatpak, not the old unpacked AppImage in
  # ~/.local/opt/bambu-studio-*; the wrapper replaces the restored one.
  apt_install flatpak
  sudo flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  sudo flatpak install -y --noninteractive flathub com.bambulab.BambuStudio
  mkdir -p "$HOME/.local/bin"
  rm -f "$HOME/.local/bin/bambu-studio"   # the restored one may be a symlink into ~/.local/opt
  printf '#!/bin/sh\n# Bambu Studio = system Flatpak (hosts/mbp-dev/install.sh section b)\nexec flatpak run com.bambulab.BambuStudio "$@"\n' \
    >"$HOME/.local/bin/bambu-studio"
  chmod 755 "$HOME/.local/bin/bambu-studio"
}

# ---------------------------------------------------------------------------
sec_c() {
  say "c. power: tlp, thermald, mbpfan"
  # tlp Conflicts with power-profiles-daemon (GNOME's default): apt removes
  # power-profiles-daemon here, and the GNOME power-mode switch goes away.
  apt_install tlp thermald mbpfan
  # tlp turns wifi power save back on by default; brcmfmac drops throughput with it.
  printf 'WIFI_PWR_ON_AC=off\nWIFI_PWR_ON_BAT=off\n' | put 0644 /etc/tlp.d/90-kdk-wifi-powersave.conf
  sudo systemctl enable --now tlp thermald mbpfan
  sudo tlp start >/dev/null
}

# ---------------------------------------------------------------------------
sec_d() {
  say "d. wifi profile: 5 GHz only, power save off"
  local uuid
  # UUID, not the name: no SSID in this script, and UUIDs have no ':' to escape.
  uuid=$(nmcli -t -f UUID,TYPE connection show --active | awk -F: '$2=="802-11-wireless"{print $1; exit}')
  if [ -z "$uuid" ]; then
    echo "  no active wifi connection: connect in GNOME once, then re-run: install.sh d"
    return 0
  fi
  sudo nmcli connection modify "$uuid" 802-11-wireless.band a 802-11-wireless.powersave 2
  echo "  set on connection $uuid; takes effect on the next connect (nmcli connection up $uuid)"
}

# ---------------------------------------------------------------------------
# cred DEST OPREF USER: cifs credentials file (username= + password=), root 0600.
cred() {
  local dest=$1 ref=$2 user=$3
  if sudo test -s "$dest"; then echo "  $dest exists, kept"; return 0; fi
  if ! { echo "username=$user"; op read "$ref" | sed 's/^/password=/'; } \
       | sudo install -D -m 600 -o root -g root /dev/stdin "$dest"; then
    sudo rm -f "$dest"
    die "could not write $dest from 1Password"
  fi
  echo "  wrote $dest ($(sudo stat -c %s "$dest") bytes)"
}

sec_e() {
  say "e. host units and credential files"
  command -v op >/dev/null || die "op (1Password CLI) not installed: run section b first"
  if ! op read -n 'op://kdk-ops/mbp-dev-restic-repo/password' >/dev/null 2>&1; then
    die "op read cannot read the kdk-ops vault: sign in to the 1Password app, turn on
  Settings -> Developer -> Integrate with 1Password CLI, then re-run: install.sh e"
  fi
  # mount.cifs lives in /usr/sbin, which is not on a normal user's PATH on Debian.
  command -v mount.cifs >/dev/null || [ -x /usr/sbin/mount.cifs ] || [ -x /sbin/mount.cifs ] \
    || die "cifs-utils missing: run section a first"

  sudo install -d -m 700 /etc/cifs /etc/restic
  cred /etc/cifs/3d-prints.cred 'op://kdk-ops/kdk-nas-01-cad-sync-smb/password' cad-sync
  cred /etc/cifs/laptop-backup.cred 'op://kdk-ops/kdk-nas-01-laptop-backup-smb/password' laptop-backup
  if sudo test -s /etc/restic/laptop-backup.pass; then
    echo "  /etc/restic/laptop-backup.pass exists, kept"
  elif op read -n 'op://kdk-ops/mbp-dev-restic-repo/password' \
         | sudo install -m 600 -o root -g root /dev/stdin /etc/restic/laptop-backup.pass; then
    echo "  wrote /etc/restic/laptop-backup.pass ($(sudo stat -c %s /etc/restic/laptop-backup.pass) bytes)"
  else
    sudo rm -f /etc/restic/laptop-backup.pass
    die "could not write /etc/restic/laptop-backup.pass from 1Password"
  fi

  # System units (root 0644), script and excludes.
  local u
  for u in 'mnt-3d\x2dprints.mount' 'mnt-3d\x2dprints.automount' \
           'mnt-laptop\x2dbackup.mount' 'mnt-laptop\x2dbackup.automount' \
           laptop-backup.service laptop-backup.timer; do
    put 0644 "/etc/systemd/system/$u" <"$HERE/systemd/$u"
  done
  put 0755 /usr/local/sbin/laptop-backup.sh <"$HERE/laptop-backup.sh"
  put 0644 /etc/restic/laptop-backup.excludes <"$HERE/laptop-backup.excludes"
  sudo systemctl daemon-reload
  sudo systemctl enable --now 'mnt-3d\x2dprints.automount' 'mnt-laptop\x2dbackup.automount' laptop-backup.timer

  # User side: cad-sync + offsite-mount scripts, cad-sync user units.
  install -D -m 755 "$HERE/cad-sync.sh" "$HOME/.local/bin/cad-sync.sh"
  install -D -m 755 "$HERE/offsite-mount.sh" "$HOME/.local/bin/offsite-mount.sh"
  install -D -m 644 "$HERE/systemd-user/cad-sync.service" "$HERE/systemd-user/cad-sync.timer" \
    -t "$HOME/.config/systemd/user/"
  systemctl --user daemon-reload
  systemctl --user enable --now cad-sync.timer
  echo "  check: systemctl list-timers laptop-backup.timer; systemctl --user list-timers cad-sync.timer"
}

# ---------------------------------------------------------------------------
sec_f() {
  say "f. GNOME: battery percentage"
  gsettings set org.gnome.desktop.interface show-battery-percentage true
}

# ---------------------------------------------------------------------------
# Node 24 LTS, not 26: the pro-workflow Claude Code plugin's better-sqlite3 has
# no prebuilt binary for Node 26. After a Node change run `npm rebuild
# better-sqlite3` in ~/.claude/plugins/cache/pro-workflow/pro-workflow/<version>/
# (by hand, see laptop-rebuild.md "Found on trixie").
sec_g() {
  say "g. Node.js 24 LTS per-user"
  local opt=$HOME/.local/opt bin=$HOME/.local/bin v f b
  if [ -x "$opt/node/bin/node" ] && "$opt/node/bin/node" --version | grep -q '^v24\.'; then
    echo "  node $("$opt/node/bin/node" --version) already in $opt/node, kept"
    return 0
  fi
  command -v python3 >/dev/null || die "python3 missing"
  # index.json is newest first; lts is false or the codename.
  v=$(curl -fsSL https://nodejs.org/dist/index.json | python3 -c '
import json, sys
for r in json.load(sys.stdin):
    if r["version"].startswith("v24.") and r["lts"]:
        print(r["version"]); break')
  [ -n "$v" ] || die "no v24 LTS release found in nodejs.org/dist/index.json"
  f=node-$v-linux-x64.tar.xz
  curl -fsSL -o "$TMPD/$f" "https://nodejs.org/dist/$v/$f"
  curl -fsSL -o "$TMPD/SHASUMS256.txt" "https://nodejs.org/dist/$v/SHASUMS256.txt"
  (cd "$TMPD" && grep " $f\$" SHASUMS256.txt | sha256sum -c --status) || die "sha256 mismatch for $f"
  mkdir -p "$opt" "$bin"
  tar -xJf "$TMPD/$f" -C "$opt"
  ln -sfn "$opt/node-$v-linux-x64" "$opt/node"
  for b in node npm npx; do ln -sfn "$opt/node/bin/$b" "$bin/$b"; done
  echo "  node $v in $opt/node, linked into $bin"
}

# ---------------------------------------------------------------------------
[ "$(id -u)" -ne 0 ] || die "run as $ME, not root (it calls sudo itself)"
[ "$(id -un)" = "$ME" ] || die "run as $ME"

SECTIONS=("$@")
[ ${#SECTIONS[@]} -gt 0 ] || SECTIONS=(a b c d e f g)
for s in "${SECTIONS[@]}"; do
  case "$s" in a|b|c|d|e|f|g) ;; *) die "unknown section '$s' (a-g)";; esac
done

sudo -v
for s in "${SECTIONS[@]}"; do "sec_$s"; done
say "done: ${SECTIONS[*]}"
