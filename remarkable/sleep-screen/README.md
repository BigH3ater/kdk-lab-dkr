# reMarkable Paper Pro sleep screen

The device shows `/usr/share/remarkable/suspended.png` on sleep. Native format:
**1620×2160 PNG** (portrait). Rootfs is read-only + persists across reboots; only
an OTA firmware update resets it (OTA is blocked at AdGuard), so a custom image is
durable. Reapply after any firmware update.

## Install a provided image (the bundle PNGs)
1. Put the image at `incoming/<name>.png` (or scp straight to the tablet).
2. Fit it to the device format:  `python fit-sleep.py incoming/<name>.png kodiak-sleep.png`
   (skips if already 1620×2160; center-crops otherwise. Bundle art is usually
   already Paper-Pro-sized, so this is a no-op resize + compaction.)
3. Push + install (backs up the stock image once, remounts rootfs rw):
   `scp kodiak-sleep.png root@<tablet>:/home/root/ && ssh root@<tablet> 'sh /home/root/install-sleep-screen.sh'`

`install-sleep-screen.sh` is re-runnable + reversible (`suspended.png.orig` backup).
To revert to stock: `mount -o remount,rw / && cp .../suspended.png.orig .../suspended.png`.

## Generated fallback
`build_sleep.py` renders a Kodiak-brand screen (photo cover from
../pdf-templates/assets, or edit for vector). Not the watercolor-illustration style.
