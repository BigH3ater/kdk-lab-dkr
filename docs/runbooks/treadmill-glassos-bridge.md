---
title: Treadmill → Home Assistant (GlassOS/DIRCON bridge)
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
    treadmill-workouts:
      mode: yaml
      title: Workouts
      icon: mdi:run
      show_in_sidebar: true
      filename: dashboards/workout.yaml
```

Then restart Home Assistant (Developer Tools → YAML → Restart, or
`action: homeassistant.restart`). Slice 2's `treadmill.yaml` package is a
drop-in under `config/packages/` (auto-included); its first activation also
needs a one-time HA restart because it introduces new entities.

## Troubleshooting

- **Device shows unavailable in HA** — the bridge publishes
  `kdk/treadmill/status = offline` when the DIRCON socket drops (console reboot,
  NordicFTMS restart, belt powered off). It retries with backoff; check
  `sudo docker logs treadmill-bridge`.
- **No states while walking** — confirm NordicFTMS is running on the console and
  the console is reachable: `ssh kdkadmin@10.1.30.21 'nc -vz 10.1.30.103 36866'`.
- **Container unhealthy** — the heartbeat is stale; the process is wedged. The
  watchdog restarts it (`restart: unless-stopped`); read the logs for why.
