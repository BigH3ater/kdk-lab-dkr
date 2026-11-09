---
title: Treadmill console HUD (MackTrack)
updated: 2026-11-08
---

# Treadmill console HUD (MackTrack)

The NordicTrack Commercial 1250 console runs **MackTrack**, the kdk build of thud
(`io.github.avikulin.thud`) from the fork `BigH3ater/thud`. It is the treadmill's
only lab integration: manual control and the on-console HUD. Since 2026-11-08 the
treadmill sends nothing to Home Assistant (the DIRCON to MQTT bridge, the HA
package and the Treadmill dashboard were retired by owner decision; see
[the retired bridge page](treadmill-glassos-bridge.md)).

**Related:** [linux-tools share](linux-tools-share.md) (the kit on kdk-dkr-04) ·
[[kdk-dkr-04]]

## Prerequisites on the console (owner, one-time)

- **NordicFTMS stays installed** (`com.nordicftms.app`, official mikepugh
  release). Its APK is the source of the GlassOS certs in the kit
  ([linux-tools share](linux-tools-share.md) step 4c), and `tm-smoke.sh` still
  checks that it runs and that its DIRCON port 36866 is open.
- **DHCP reservation** for the console (`10.1.30.103` in UniFi), so the kit's adb
  connect finds it at a fixed address.

## Build, install, update

MackTrack runs alongside NordicFTMS and does not replace it. It has one loop:

- **Build:** GitHub Actions on the fork (`.github/workflows/build.yml`). Nothing
  is built on the laptop.
- **Install and test:** on kdk-dkr-04 as `kdkadmin`, with the fork's
  `kdk/tm-*.sh` scripts from the linux-tools share
  (`KIT=/mnt/linux-tools/thud`). The scripts take the APK from the release and the
  certs and confs from `$KIT/secrets/` and `$KIT/conf/`. Those are seeded from
  1Password per [linux-tools share](linux-tools-share.md). Never copy certs by
  hand, and never paste their contents, the HA webhook id or the keystore password
  anywhere.

Script reference: `kdk/README.md` in the fork.

### Build

| Trigger | Result |
|---|---|
| push to `feat/kdk-integration` (or a PR against it) | debug build, APK as a workflow artifact, no release |
| tag `kdk-vX.Y.Z` | signed **pre-release** with `thud-kdk-vX.Y.Z.apk` + `.sha256` |

`versionCode` is the commit count and `versionName` is `git describe`. Tag builds
are signed with the kdk key. The keystore is in 1Password item `thud-kdk-signing`
and reaches the workflow only as GitHub secrets. The run logs the signer SHA-256,
which must match the fingerprint noted on that item (`94ca90fe…4e20`).

**Laptop as jmack, release a new version** (in a clone of the fork)

```sh
# laptop as jmack: tag a release on the fork
cd /bench/thud
git switch feat/kdk-integration && git pull --ff-only
TAG=kdk-v1.0.1                       # next version
git tag "$TAG" && git push origin "$TAG"
gh run list -R BigH3ater/thud --workflow build.yml --limit 3
gh release view "$TAG" -R BigH3ater/thud --json assets --jq '.assets[].name'   # apk + apk.sha256
```

### One-time pairing

Pairing is needed once per console (after a factory reset or a revoked pairing).
At the console, open Settings → Developer options → **Wireless debugging** →
*Pair device with pairing code* and note the pairing port and the code.

```sh
# kdk-dkr-04 as kdkadmin (ssh kdkadmin@10.1.20.22): pair adb with the console
cd /mnt/linux-tools/thud/src
PAIR_PORT=38447                       # replace: port from the console's pairing dialog
PAIR_CODE=123456                      # replace: one-time code from the same dialog (expires, not stored)
kdk/tm-adb.sh pair "$PAIR_PORT" "$PAIR_CODE"
```

Wireless debugging has to be **on** at the console for every install. The
connect port changes after a reboot, so `tm-adb.sh` tries the saved port in
`$KIT/state/adb-port` first and otherwise scans for it (it skips DIRCON 36866).

### Install or update

```sh
# kdk-dkr-04 as kdkadmin (ssh kdkadmin@10.1.20.22): install the newest kdk-v* release
cd /mnt/linux-tools/thud/src
git pull --ff-only
eval "$(kdk/tm-adb.sh)"               # pins ANDROID_SERIAL to the console
kdk/tm-fetch.sh latest                # or a tag: kdk/tm-fetch.sh kdk-v1.0.1
kdk/tm-install.sh                     # adb install -r: keeps profiles, history, certs
```

`tm-install.sh` verifies the sha256 and backs up the app data to
`$KIT/backup/` (it keeps 5). It then runs `adb install -r` and re-places the certs
and confs only when they are missing or changed. Last, it restarts the app and
runs the smoke check.

**Signing key change only.** If the install stops on
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the installed app has a different signer.
Re-run with `kdk/tm-install.sh --allow-uninstall`. It uninstalls, installs and
restores the backup it just took. Never use `--allow-uninstall` for a normal
update.

### Smoke

```sh
# kdk-dkr-04 as kdkadmin (ssh kdkadmin@10.1.20.22): read-only health check
cd /mnt/linux-tools/thud/src
eval "$(kdk/tm-adb.sh)"
kdk/tm-smoke.sh                       # exit 0 = no misses; log in /mnt/linux-tools/thud/state/
```

It checks the version, the process, the HUDService, the top activity, that MackTrack
is the **default launcher**, the cert sizes (1265/1090/1704 B), the logcat lines
`HA integration ENABLED`, `Using INTERNAL certs dir` and
`KdkSmoke: glassos connected`, that NordicFTMS is running and that DIRCON 36866 is
open. Then do a short walk on the belt and confirm the HUD tracks it.

The `HA integration ENABLED` line and the `kdk-ha.conf` the installer places are
fork behaviour that predates the 2026-11-08 retirement of the Home Assistant side:
the HUD may still POST profile events to the old HA webhook, which no longer exists
(HA drops them). Turning that off is a change in the fork, not in this repo.

### Rollback

```sh
# kdk-dkr-04 as kdkadmin (ssh kdkadmin@10.1.20.22): reinstall the previous fetched release
cd /mnt/linux-tools/thud/src
eval "$(kdk/tm-adb.sh)"
PREV="$(ls /mnt/linux-tools/thud/apk/previous/thud-kdk-*.apk)"
kdk/tm-install.sh --allow-uninstall "$PREV"
```

This is a downgrade, so it needs the uninstall path. The app data restored is
from the newer build. If the older build fails to open it, see the backups in
`/mnt/linux-tools/thud/backup/`.

### Troubleshooting (console app)

- **Smoke: "default launcher is not io.github.avikulin.thud"**: an uninstall
  (key change, rollback) clears the Home role. Newer kit scripts re-set it
  themselves. By hand:

  ```sh
  # kdk-dkr-04 as kdkadmin (ssh kdkadmin@10.1.20.22): make MackTrack the Home app again
  cd /mnt/linux-tools/thud/src
  eval "$(kdk/tm-adb.sh)"
  ADB=/mnt/linux-tools/platform-tools/37.0.1/adb
  "$ADB" shell cmd package set-home-activity io.github.avikulin.thud/.MainActivity   # Success
  "$ADB" shell cmd shortcut get-default-launcher                                      # names io.github.avikulin.thud
  ```

- **Smoke: "HUDService: no ServiceRecord"** right after an install: the service
  starts a few seconds after the process. Re-run `kdk/tm-smoke.sh`.
- **`tm-adb.sh` finds no console**: wireless debugging is off, or the pairing
  was lost. Turn it on, or pair again (above).

