---
title: Home Assistant — dashboards & integrations
host: kdk-dkr-02
stack: home-assistant
tier: apps
status: live
storage: yes
secrets: [home-assistant-nodered-token, node-red-admin, node-red-credential-secret, zigbee2mqtt-frontend-token, kdk-ups-01-nut-upsd, scrypted-admin, homeassistant-oidc-client, vikunja-api-ro]
updated: 2026-10-28
tags: [homelab/stack, tier/apps, status/live]
---

# Home Assistant — dashboards & integrations

Home Assistant runs on [[kdk-dkr-02]] (network_mode host) with Node-RED
alongside it. **Dashboards, themes, and packages are GitOps-owned** since
2026-09-21: `stacks/home-assistant/config/{dashboards,themes,packages}` is
copied into `/opt/kdk-lab/home-assistant/config/` by the stack's Komodo
`pre_deploy`. So is Node-RED's `settings.js` (`stacks/home-assistant/node-red/`).
The rest of the HA config (`configuration.yaml`, `.storage`, `secrets.yaml`,
`custom_components`) and the Node-RED flow stay host-side under
`/opt/kdk-lab/home-assistant/`.

**2026-10-28 hardening:** Node-RED editor/admin API behind `adminAuth` with
encrypted credentials and audit logging; Node-RED talks to HA as a dedicated
local-only `node-red` user (admin, required by the websocket nodes) instead of the owner's token; Zigbee2MQTT frontend behind an auth token, log level
`info`; Scrypted 0.123 → 0.147 (GHSA-fgj8-7r76-h852); Homebridge removed. HA 2026.8.3 → **2026.10.4** the same day (PR #77): the Map panel's CARTO tiles showed "API KEY REQUIRED"; 2026.9+ serves OpenStreetMap tiles through core's tile proxy.

**Related:** [[kdk-dkr-02]] · [[nut]] · [[observability]] · [[dashboard]]

## At a glance

| Field | Value |
|---|---|
| Host | [[kdk-dkr-02]] (10.1.30.21, VLAN 30), HA + Node-RED, HomeKit bridge |
| Login | **Authelia OIDC** (`hass-oidc-auth` custom component, client `homeassistant`, Authelia policy `ha_access` = LLDAP group **`ha-users`** with 2FA; `ha-admins` → HA admin role); local login kept as fallback (usernames `jmack`/`rach`) |
| Theme | **Kodiak** (INK GROUND dark, `config/themes/kodiak.yaml`), set as backend default on start |
| Dashboards | **Home** (iOS-Home-style rooms, sections+tile), **Roborock**, **Homelab** — YAML-mode, registered in `configuration.yaml` |
| Home controls | per-room sections: blinds (Zigbee covers), theater lights (+group), T9 thermostat, ceiling fan, outlets, Bosch dishwasher, HomePods/Apple TVs |
| Homelab telemetry | REST sensors pulling [[observability]] Prometheus (`10.1.20.30:9090`): iDRAC fans/temps/power + per-host CPU/mem/uptime, plus the [[nut]] UPS |
| Roborock | fully local vacuum — live map (tap-to-clean), per-room buttons, routines, per-room last-cleaned stamps, next-run; automation in Node-RED |
| Maintenance chores | **Vikunja is the single source of truth** (tasks.kmkdp.com project 6); HA shows them via a command_line sensor; Node-RED bridges completions both ways |
| Other integrations | Apple TV ×several, Lutron Caséta, Zigbee2MQTT (Sonoff dongle), HomeKit bridge/controller, [[nut]] UPS |
| Node-RED | `nodered.kmkdp.com`; editor + admin API need a login (`adminAuth`, user `jmack`, bcrypt hash in host `node-red.env`); `flows_cred.json` encrypted with `credentialSecret`; admin API audit events in the container log; HA access through the dedicated, local-only HA user `node-red` (Administrator **on**: the websocket nodes refused a non-admin user on 2026-10-28, "User required to have admin privileges") |
| Zigbee2MQTT | `zigbee.kmkdp.com`; the frontend asks for the auth token once per browser (`ZIGBEE2MQTT_CONFIG_FRONTEND_AUTH_TOKEN` from host `zigbee2mqtt.env`); `advanced.log_level: info` set by env override in compose |
| Scrypted | on [[kdk-dkr-03]], v0.147.0 (`-noble-full`); UI ports 10443/11080 are reachable from VLAN 30 (the planned UniFi rule was dropped 2026-10-28: dkr-02 and dkr-03 share VLAN 30, so the gateway cannot filter that path; the 0.147 upgrade is the fix). Cameras reach HA only via HomeKit today (no video); the `koush/ha_scrypted` HACS integration is installed, pending the correct Scrypted login |

## Dashboards

- **Home** (`/home-controls`) — sections per room (Living Room, Kitchen,
  Dishwasher, Master Bedroom, Master Bathroom, Bedrooms, Theater, Basement,
  Garage, Vacuum), tile cards with features, emulating the iOS Home app.
  The family reaches it same-tab from the home.kmkdp.com Kodiak sidebar.
- **Roborock** (`/roborock-vac`) — live map (HACS `lovelace-xiaomi-vacuum-map-card`,
  manual calibration — do not re-derive), status/schedule/next-run, rooms-cleaned
  stamps, routines, per-room clean, Vikunja chore list + dock sensors.
- **Homelab** (`/homelab-hw`) — iDRAC gauges for [[kdk-hyp-01]] and
  [[kdk-nas-01]], the [[nut]] UPS, and CPU/memory/uptime for all monitored hosts.

## Roborock ↔ Vikunja bridge (Node-RED)

The old duplicate schedulers (Node-RED maintenance timers + `rr_maint_*`
booleans in HomeKit + a dashboard card) were removed 2026-09-21. Now:

- Vikunja project 6 ("Robot Vacuum") holds the recurring chores
  (repeat-from-completion).
- Node-RED polls the project every 5 min; when a chore is completed in
  Vikunja it presses the matching consumable-reset buttons (brush/filter/bag).
- Dock-sensor evidence auto-completes "Fill water" (water-shortage clears /
  clean tank returns).
- Node-RED stamps `input_datetime.rr_last_clean_<room>` when it dispatches
  rooms (it is the sole dispatcher) and `rr_next_run` from the schedule.

## Verify

```sh
# HA up + a homelab sensor populated (from the laptop; token never touches the Pi)
curl -s -H "Authorization: Bearer $(KDK_OP_MODE=sa op read op://kdk-cluster/home-assistant-nodered-token/credential)" \
  https://homeassistant.kmkdp.com/api/states/sensor.kdk_hyp_01_cpu_temp

# Node-RED admin API and Z2M frontend refuse anonymous callers
curl -s -o /dev/null -w '%{http_code}\n' https://nodered.kmkdp.com/flows     # 401
curl -s --http1.1 -m 3 -N -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  https://zigbee.kmkdp.com/api | strings | grep -c Unauthorized             # 1
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
- **Chore card says unavailable** — the command_line sensor needs
  `/config/.vikunja_token` (host-managed, 0600) and tasks.kmkdp.com reachable.
- Editing an existing YAML dashboard file is picked up on browser refresh;
  **registering a new dashboard or the themes dir needs an HA restart**;
  package edits reload with Developer Tools → "Reload all YAML".
- **OIDC login loop / missing button** — check `auth_oidc:` in
  `configuration.yaml`, the Authelia client `homeassistant`
  (PKCE S256 + `client_secret_post`, policy `ha_access`), and that the user is
  in the LLDAP group `ha-users` (Authelia refuses everyone else). Host-side
  `auth_oidc:` should carry `features: {automatic_user_linking: false}` and
  `roles: {user: ha-users, admin: ha-admins}` (both humans are already linked,
  and without `roles` HA falls back to "anyone" for users and a group literally
  named `admins` for admins).

## Configuration

- Dashboards/themes/packages: **repo** `stacks/home-assistant/config/…` →
  pre_deploy copies to `/opt/kdk-lab/home-assistant/config/…` (url path must
  contain a hyphen; new dashboards need an HA restart).
- SSO: `config/packages/auth_oidc.yaml` is **repo-owned** (since 2026-10-28;
  before that the block lived in `configuration.yaml`). The client secret is
  `!secret oidc_client_secret` in the host `secrets.yaml`.
- Host-side: `configuration.yaml` (only the includes, `default_config`,
  `lovelace` dashboard registrations and the `packages:` include), `secrets.yaml`,
  `/config/.vikunja_token`, `custom_components/auth_oidc`, `.storage/`.
- Adding an SSO user: create the HA user with the same username as LLDAP, add the
  LLDAP user to `ha-users`, set `automatic_user_linking: true` in the package,
  deploy + restart, have them log in once, set it back to `false`, deploy + restart.
- Node-RED: `/opt/kdk-lab/home-assistant/node-red/` (Roborock automation +
  Vikunja bridge; token in the "vik auth" function node). `settings.js` is
  **repo-owned** — pre_deploy copies it in; Node-RED reads it only at start, so
  a settings-only change needs a node-red restart. Secrets for it live in the
  host-managed `/opt/kdk-lab/home-assistant/node-red.env` (root 600); the Z2M
  frontend token in `zigbee2mqtt.env` beside it. bcrypt hashes in those files
  must be single-quoted (compose interpolates `$` otherwise).
- HomeKit: HA entities are exposed through the existing paired "HASS Bridge"
  (include list in `.storage/core.config_entries` — stop HA before editing).

## Secret Rotation

`home-assistant-nodered-token` (Node-RED → HA, issued by the HA user
`node-red`), `node-red-admin` (editor login; bcrypt hash in
`node-red.env`), `node-red-credential-secret` (**do not rotate casually**:
a new value makes the existing `flows_cred.json` unreadable, so every
credential must be re-entered), `zigbee2mqtt-frontend-token` (`zigbee2mqtt.env`,
then recreate zigbee2mqtt), `kdk-ups-01-nut-upsd` (NUT
client), `homeassistant-oidc-client` (Authelia client secret; digest in the
DMZ `/secrets/authelia-app-secrets/homeassistant_client_secret`, plaintext in
HA `secrets.yaml`), `vikunja-api-ro` (chore sensor + Node-RED bridge),
`scrypted-admin` (once the Scrypted integration is completed).
