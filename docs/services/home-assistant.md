---
title: Home Assistant — dashboards & integrations
host: kdk-dkr-02
stack: home-assistant (adopted)
tier: apps
status: live
storage: yes
secrets: [home-assistant-nodered-token, kdk-ups-01-nut-upsd, scrypted-admin]
updated: 2026-09-15
tags: [homelab/stack, tier/apps, status/live]
---

# Home Assistant — dashboards & integrations

Home Assistant runs on [[kdk-dkr-02]] (network_mode host) with Node-RED
alongside it. Its config and the Node-RED flow are **host-side app data** under
`/opt/kdk-lab/home-assistant/` — not in the GitOps repo. Dashboards are
YAML-mode; sensors are HA packages.

**Related:** [[kdk-dkr-02]] · [[nut]] · [[observability]]

## At a glance

| Field | Value |
|---|---|
| Host | [[kdk-dkr-02]] (10.1.30.21, VLAN 30), HA + Node-RED, HomeKit bridge |
| Dashboards | **Home**, **Homelab**, **Roborock** (YAML-mode, registered in `configuration.yaml`) |
| Home controls | blinds (Zigbee covers, tile cards), lights, T9 thermostat, ceiling fan, outlets, Bosch dishwasher (Home Connect), media players |
| Homelab telemetry | REST sensors pulling [[observability]] Prometheus (`10.1.20.30:9090`): iDRAC fans/temps/power + per-host CPU/mem/uptime, plus the [[nut]] UPS |
| Roborock | fully local vacuum (map card, per-room clean, schedules, maintenance) — automation lives in Node-RED |
| Other integrations | Apple TV ×several, Lutron Caséta, ZHA (Zigbee), HomeKit bridge/controller, [[nut]] UPS |
| Scrypted | cameras reach HA only via HomeKit today (no video); the `koush/ha_scrypted` HACS integration is installed, pending the correct Scrypted login |

## Dashboards

- **Home** — thermostat, all blinds (open/close + position), lights, fan,
  outlets, the Bosch dishwasher (status/program/start/options), and media.
- **Homelab** — iDRAC gauges for [[kdk-hyp-01]] (R720) and [[kdk-nas-01]]
  (R730xd): CPU temp, inlet, power, fan avg/max; the [[nut]] UPS (battery,
  load, status); and CPU/memory/uptime for all 11 monitored hosts.
- **Roborock** — the live map (HACS vacuum-map card, tap-to-clean rooms),
  per-room buttons, routines, and maintenance reminders.

## Verify

```sh
# HA up + a homelab sensor populated
ssh kdkadmin@10.1.30.21 'curl -s -H "Authorization: Bearer $(sudo cat \
  /opt/kdk-lab/home-assistant/node-red/flows_cred.json | \
  python3 -c "import sys,json;print(json.load(sys.stdin)[chr(39)+\"38978576.ca0eea\"+chr(39)][\"access_token\"])")" \
  http://10.1.30.21:8123/api/states/sensor.kdk_hyp_01_cpu_temp'
```

## Troubleshooting

- **A card control does nothing** — check the entity's `supported_features`;
  covers need OPEN/CLOSE/SET_POSITION. Prefer `tile` cards with features over
  `entities` rows for covers/vacuums.
- **Homelab sensors `unavailable`** — HA must reach Prometheus at
  `10.1.20.30:9090`; the value template returns `unavailable` on an empty
  query result.
- **Server temps show °F** — omit `device_class: temperature` (HA's imperial
  unit system converts it); keep unit `°C` + `state_class: measurement`.
- Editing an existing YAML dashboard file is picked up on browser refresh;
  **registering a new dashboard needs an HA restart**.

## Configuration

- Dashboards: `/opt/kdk-lab/home-assistant/config/dashboards/*.yaml`, registered
  under `lovelace.dashboards` in `configuration.yaml` (url path must contain a
  hyphen).
- Sensors: `config/packages/homelab.yaml` (REST → Prometheus).
- Node-RED: `/opt/kdk-lab/home-assistant/node-red/` (Roborock automation).
- HomeKit: HA entities are exposed through the existing paired "HASS Bridge".

## Secret Rotation

`home-assistant-nodered-token` (Node-RED → HA), `kdk-ups-01-nut-upsd` (NUT
client), `scrypted-admin` (once the Scrypted integration is completed).
