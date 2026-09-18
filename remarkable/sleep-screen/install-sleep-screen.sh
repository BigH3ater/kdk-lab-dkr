#!/bin/sh
# Install a custom Kodiak sleep screen on the reMarkable Paper Pro.
# Run ON the tablet after copying the PNG to it (default /home/root/kodiak-sleep.png).
#
# Re-runnable + reversible: backs up the stock image ONCE to suspended.png.orig,
# remounts the read-only rootfs rw, installs, remounts ro. The rootfs persists
# across reboots (unlike /etc,/var/lib tmpfs overlays); only an OTA firmware
# update would reset it (OTA is blocked at AdGuard), so reapply this after any
# firmware change. Revert: cp suspended.png.orig suspended.png (rootfs rw first).
#
# Image must be 1620x2160 PNG (RGBA), the native suspended.png format.
set -e
SRC="${1:-/home/root/kodiak-sleep.png}"
DST=/usr/share/remarkable/suspended.png
[ -f "$SRC" ] || { echo "ERROR: source image not found: $SRC" >&2; exit 1; }

mount -o remount,rw /
[ -f "$DST.orig" ] || cp -a "$DST" "$DST.orig"   # one-time backup of the stock image
cp "$SRC" "$DST"
chmod 644 "$DST"
sync
mount -o remount,ro /
echo "Installed sleep screen: $SRC -> $DST"
echo "Backup of stock image:  $DST.orig"
echo "It appears on the next sleep (no reboot needed)."
