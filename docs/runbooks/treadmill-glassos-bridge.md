---
title: Treadmill → Home Assistant (GlassOS/DIRCON bridge)
updated: 2026-11-08
---

# Treadmill → Home Assistant (GlassOS/DIRCON bridge)

The `treadmill-bridge` stack (kdk-dkr-02) reads live telemetry from the
NordicTrack Commercial 1250 console and republishes it to Home Assistant over
MQTT. It is **read-only**: it reads DIRCON notifications and issues no motor
command. This page covers the parts that are **not** in the repo — the console
app, the DHCP reservation, the (optional) broker auth, and the deploy order.

The work ships in three slices, one PR each, validated on the host between them:

1. **`treadmill-bridge` stack + `komodo/resources.toml` registration** (this
   runbook) — the bridge and the `sensor.treadmill_*` MQTT-discovery entities.
2. **`stacks/home-assistant/config/packages/treadmill.yaml`** — the multi-user
   tracking package. It references the discovered sensors, so it only works once
   slice 1 is live and publishing.
3. **`stacks/home-assistant/config/dashboards/workout.yaml`** — the workout
   dashboard (needs the host-side `lovelace:` registration in step 5).

## Prerequisites on the console (owner, one-time)

- **NordicFTMS must stay installed and running** on the console
  (`com.nordicftms.app`, official mikepugh release). It exposes the GlassOS gRPC
  telemetry as the DIRCON server the bridge reads. It runs alongside iFit, does
  not replace the UI, and auto-restarts on boot. Removing it (`adb uninstall
  com.nordicftms.app`) stops the bridge's data source.
- **DHCP reservation for the console.** The bridge targets a fixed IP
  (`TREADMILL_HOST`, default `10.1.30.103`) on the hardcoded DIRCON port
  `36866`. Reserve the console's MAC → `10.1.30.103` in UniFi so it never moves.
  After a console reboot the DIRCON server comes back on its own; the bridge
  reconnects with backoff and needs no ADB.

## Console app (MackTrack): build, install, update

The HUD on the console is **MackTrack**, the kdk build of thud
(`io.github.avikulin.thud`) from the fork `BigH3ater/thud`. It runs alongside
NordicFTMS and does not replace it. It has one loop:

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
open. Then do a short walk on the belt and confirm the HUD and HA both track it.

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

## Deploy order (slice 1)

The `komodo/resources.toml` `[[stack]]` for `treadmill-bridge` must reach Komodo
**before** the first deploy — a stack that is not in the synced resource file
does not exist to Komodo.

1. Merge the PR. The `gitops-sync` procedure (every 15 min, or run it from
   `komodo.kmkdp.com`) applies the toml via `RunSync`.
2. The stack's `pre_deploy` creates `/opt/kdk-lab/treadmill-bridge/state` owned
   `1000:1000` (the container runs unprivileged and writes its heartbeat there).
3. `run_build = true` builds `kdk/treadmill-bridge:latest` on kdk-dkr-02; the
   bridge starts under `network_mode: host`.

Verify (belt moving):

```bash
ssh kdkadmin@10.1.30.21 'sudo docker ps --filter name=treadmill-bridge'
# retained discovery + live states on the loopback broker:
ssh kdkadmin@10.1.30.21 "sudo docker exec mosquitto mosquitto_sub -h 127.0.0.1 \
  -t 'homeassistant/sensor/treadmill_#' -t 'kdk/treadmill/#' -v -W 10"
```

Home Assistant → Settings → Devices should then list a **Treadmill** device
with `sensor.treadmill_speed`, `sensor.treadmill_distance`,
`sensor.treadmill_incline`. The container stays healthy on the
`/state/bridge.heartbeat` healthcheck even while the belt is stopped (the bridge
keeps the DIRCON subscription open and the heartbeat fresh).

## Optional: Mosquitto broker auth

Mosquitto on kdk-dkr-02 listens on `127.0.0.1:1883` only and is **anonymous** on
that loopback listener, so by default the bridge needs **no secret** — it
publishes exactly like zigbee2mqtt does. Only if broker auth is ever turned on:

1. Create the secret (laptop; see [Rotate a secret](rotate-a-secret.md)):

   ```bash
   op item create --vault kdk-cluster --category password \
     --title treadmill-bridge-mqtt --generate-password=32,letters,digits
   ```

2. Add a matching `treadmill` user to Mosquitto's host-managed `password_file`
   (the broker's config is host-managed on kdk-dkr-02).

3. Ship the host env file (kdk-dkr-02 has **no `op` CLI** — root 600, never
   `op inject`):

   ```bash
   printf 'MQTT_USER=treadmill\nMQTT_PASS=%s\n' \
     "$(op read op://kdk-cluster/treadmill-bridge-mqtt/password)" \
   | ssh kdkadmin@10.1.30.21 \
     'sudo install -m 600 -o root -g root /dev/stdin /opt/kdk-lab/treadmill-bridge/mqtt.env'
   ```

4. Uncomment the `env_file` block in `stacks/treadmill-bridge/compose.yaml`
   (it is `required: false`, so it is harmless while absent) and redeploy.

Never commit the env file or the password — the repo is public.

## Slice 3 host-side: register the workout dashboard

When slice 3 (the dashboard) lands, the repo's
`config/dashboards/workout.yaml` is copied into
`/opt/kdk-lab/home-assistant/config/dashboards/` by the home-assistant stack's
`pre_deploy` (same mechanism as the other YAML-mode dashboards). It still has to
be **registered** in the host-managed `configuration.yaml` (not in the repo):

```yaml
# /opt/kdk-lab/home-assistant/config/configuration.yaml  (host-managed)
lovelace:
  mode: storage
  dashboards:
    treadmill-workouts:            # -> https://homeassistant.kmkdp.com/treadmill-workouts
      mode: yaml
      title: Treadmill
      icon: mdi:run
      show_in_sidebar: true
      filename: dashboards/workout.yaml
```

The `treadmill-workouts` url_path is what the Kodiak left-sidebar **Treadmill** link
points at. The file carries the single **Treadmill** view (the Strength / Progress / Plan
views and their `ex_*` helpers were retired 2026-11-06 when wger won the tracking pilot;
strength logging is in [[wger]]). It is the one dashboard for both people since
2026-11-07: Rachel's identical copy (`workout-rachel`, `dashboards/workout_rachel.yaml`)
was dropped. Then restart Home
Assistant (Developer Tools → YAML → Restart, or `action: homeassistant.restart`).
Slice 2's `treadmill.yaml` package is a drop-in under `config/packages/`
(auto-included); its first activation also needs a one-time HA restart because it
introduces new entities.

(The former **Plan** tab iframe of `home.kmkdp.com/guides/workout/` is gone with the
guide, 2026-11-06; the plan with pictures is in the wger app.)

## Slice 2 host-side: thud → HA webhook secret + console profiles

`treadmill.yaml` includes the `tm_walker_from_thud` automation, which lets the
console set the active walker in HA automatically (no dashboard button needed).
It needs one host secret and two console profiles.

1. **Add the webhook id to the host `secrets.yaml`** (host-managed, never in the
   repo). It must match the id in the console's `files/kdk-ha.conf`
   (`webhook_url=https://homeassistant.kmkdp.com/api/webhook/<id>`), which
   `tm-install.sh` places from `$KIT/secrets/kdk-ha.conf` (1Password Document
   `thud-kdk-ha-conf`):

   ```yaml
   # /opt/kdk-lab/home-assistant/config/secrets.yaml  (host-managed)
   tm_webhook_id: thud-xxxxxxxxxxxxxxxxxxxxxxxx
   ```

   The random id is the only credential on this webhook, so it stays a
   `!secret`. Restart HA once so the `tm_walker_from_thud` webhook registers.

2. **Create the console profiles** in thud named exactly **`Jacob`** and
   **`Rachel`** (tap the User chip → Add User). These match the
   `input_select.tm_current_walker` options; any other profile maps to `Guest`.

3. **Verify:** switch the active profile on the console (or start a walk). thud
   POSTs `{"event":"profile","profile":"Jacob"}`; `input_select.tm_current_walker`
   should flip to `Jacob` within a second (Developer Tools → States, or the
   automation trace). The speed-based session automations then attribute the
   walk's distance/time/calories to that walker.

   Distance credited is the session's tracked distance
   (`input_number.tm_session_peak_distance`, metres), not the raw counter at the
   end: the console zeroes `sensor.treadmill_distance` about 4 s after the belt
   stops, 2 minutes before the END automation runs.

## Troubleshooting

- **A walk was credited 0 m** — the
  tracker did not see the counter rise during the session. Check the trace of
  `Treadmill: track session distance` (it must run while
  `input_boolean.tm_session_active` is on) and the history of
  `sensor.treadmill_distance` for that walk; a session opened before the
  tracker deployed falls back to the raw delta, which the reset makes 0.

- **Device shows unavailable in HA** — the bridge publishes
  `kdk/treadmill/status = offline` when the DIRCON socket drops (console reboot,
  NordicFTMS restart, belt powered off). It retries with backoff; check
  `sudo docker logs treadmill-bridge`.
- **No states while walking** — confirm NordicFTMS is running on the console and
  the console is reachable: `ssh kdkadmin@10.1.30.21 'nc -vz 10.1.30.103 36866'`.
- **Container unhealthy** — the heartbeat is stale; the process is wedged. The
  watchdog restarts it (`restart: unless-stopped`); read the logs for why.
