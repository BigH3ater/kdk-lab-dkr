# treadmill-bridge

A **read-only** bridge that reads live telemetry from the household NordicTrack
Commercial 1250 treadmill and republishes it to Home Assistant via MQTT
discovery. Runs on **kdk-dkr-02** (10.1.30.21, VLAN 30), the only host both on
the treadmill's L2 segment and running Mosquitto + Home Assistant.

It is **slice 1 of 3** of the treadmill→HA plan. This slice delivers the bridge
and the MQTT sensors only. The Home Assistant multi-user tracking package and
the workout dashboard land in later PRs, once the sensors are confirmed live on
the host.

## What it does

```
NordicFTMS (console, 10.1.30.103)              treadmill-bridge (this stack)
  GlassOS gRPC → DIRCON server  ── TCP 36866 ──▶  DIRCON client (read-only)
                                                      │ paho-mqtt
                                                      ▼
                                   Mosquitto 127.0.0.1:1883  ──▶  Home Assistant
                                   (MQTT discovery → sensor.treadmill_*)
```

- Connects to the NordicFTMS **DIRCON** server (Wahoo Direct Connect, plain
  TCP) on `TREADMILL_HOST:DIRCON_PORT`.
- Subscribes (DiscoverServices → EnableNotifications on FTMS Treadmill Data
  `0x2ACD`) and decodes the ~2 Hz notification stream.
- Publishes retained HA **MQTT discovery** configs for a single `Treadmill`
  device with three sensors, then streams live state:

  | Entity | Topic | Unit | device_class / state_class |
  |---|---|---|---|
  | `sensor.treadmill_speed` | `kdk/treadmill/speed` | km/h | speed / measurement |
  | `sensor.treadmill_distance` | `kdk/treadmill/distance` | m | distance / measurement |
  | `sensor.treadmill_incline` | `kdk/treadmill/incline` | % | — / measurement |

- Publishes availability to `kdk/treadmill/status` (`online`/`offline`), also
  set as the MQTT Last-Will so a crash marks the device unavailable.
- Writes `/state/bridge.heartbeat` every loop (including during reconnect
  backoff) for the compose healthcheck.

**Read-only guarantee:** `bridge.py` only ever sends DIRCON DiscoverServices and
EnableNotifications and reads Notification pushes. It writes no FTMS
control-point characteristic and issues **no motor command** — there is no code
path that sends a value to the treadmill.

## Protocol (validated live 2026-11-01)

DIRCON 6-byte header: `[0]=0x01 ver, [1]=msgType, [2]=seq, [3]=respCode,
[4:6]=payloadLen BIG-endian, payload@6`. Message types used: `0x01`
DiscoverServices, `0x05` EnableNotifications, `0x06` Notification.

Each request uses a **unique incrementing seq** — the server silently drops a
request whose seq equals the previous one. FTMS Treadmill Data `0x2ACD`: flags
`u16` LE, then speed `u16` LE ×0.01 km/h, distance `u24` LE m, incline `s16` LE
×0.1 % (ramp `s16`, `0x7FFF` = N/A, ignored).

## Configuration (env)

| Var | Default | Notes |
|---|---|---|
| `TREADMILL_HOST` | `10.1.30.103` | NordicFTMS console (give it a DHCP reservation) |
| `DIRCON_PORT` | `36866` | hardcoded in NordicFTMS |
| `MQTT_HOST` | `127.0.0.1` | loopback Mosquitto on this host |
| `MQTT_PORT` | `1883` | |
| `MQTT_USER` / `MQTT_PASS` | unset | only if the broker is not anonymous (see below) |
| `TZ` | `America/Chicago` | |

**MQTT auth:** Mosquitto's loopback listener is anonymous, so by default the
bridge needs no secret (it publishes exactly like zigbee2mqtt). If auth is ever
enabled, create the host-managed env file and uncomment the `env_file` in
`compose.yaml` — see
[`docs/runbooks/treadmill-glassos-bridge.md`](../../docs/runbooks/treadmill-glassos-bridge.md).
The file is `required: false`, so a missing file never fails the deploy.

## Deploy

GitOps via Komodo (stack `treadmill-bridge`, kdk-dkr-02). The
`komodo/resources.toml` registration must sync **before** the first deploy; its
`pre_deploy` creates `/opt/kdk-lab/treadmill-bridge/state` owned `1000:1000` so
the unprivileged container can write its heartbeat.

## Hardening

Meets `docs/reference/compose-baseline.md`
(`python3 scripts/compose-baseline-check.py --stack treadmill-bridge`). It
carries one documented exception, `host-net`, for the same reason as the
`home-assistant` service: it must reach the loopback-only Mosquitto and the
VLAN-30 treadmill at once.

## Validation

Unvalidated until deployed on kdk-dkr-02. After deploy, with the belt moving:

```bash
mosquitto_sub -h 127.0.0.1 -t 'homeassistant/sensor/treadmill_#' -t 'kdk/treadmill/#' -v
```

should show the retained discovery configs plus live `speed`/`distance`/
`incline` states, and Home Assistant should list a `Treadmill` device with
populated `sensor.treadmill_*`.
