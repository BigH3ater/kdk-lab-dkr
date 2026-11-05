---
title: Home Assistant — dashboards & integrations
host: kdk-dkr-02
stack: home-assistant
tier: apps
status: live
storage: yes
secrets: [home-assistant-nodered-token, home-assistant-roborock-map-token, node-red-admin, node-red-credential-secret, zigbee2mqtt-frontend-token, kdk-ups-01-nut-upsd, scrypted-admin, homeassistant-oidc-client, vikunja-api-ro, vikunja-api-approvals]
updated: 2026-11-05
tags: [homelab/stack, tier/apps, status/live]
---

# Home Assistant — dashboards & integrations

Home Assistant runs on [[kdk-dkr-02]] (network_mode host) with Node-RED
alongside it. **Dashboards, themes, and packages are GitOps-owned** since
2026-09-21: `stacks/home-assistant/config/{dashboards,themes,packages}` is
copied into `/opt/kdk-lab/home-assistant/config/` by the stack's Komodo
`pre_deploy`. So is Node-RED's `settings.js` (`stacks/home-assistant/node-red/`).
The Node-RED flow is in git too (`stacks/home-assistant/node-red/flows.json`, the
repo being the source of truth); it is synced to the live Node-RED with
`scripts/nodered-flow-sync.sh` (`import`/`export`/`diff`), not copied by
`pre_deploy`. The rest of the HA config (`configuration.yaml`, `.storage`,
`secrets.yaml`, `custom_components`) stays host-side under
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
| Theme | **Kodiak** (INK GROUND dark, `config/themes/kodiak.yaml`), a dark-only `modes:` theme set as backend default on start; carries the MD3 / `ha-color-*` tokens the 2026.4+ frontend uses (see [Theme tokens after 2026.4](#theme-tokens-after-20264)) |
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
- **Roborock** (`/roborock-vac`) — sections *Current status*, *Rooms*, *Mop run*,
  *Alerts*, *Maintenance*, plus the **Map** tab (HACS
  `lovelace-xiaomi-vacuum-map-card`, manual calibration — do not re-derive;
  rooms numbered by segment id with a Key card below). See
  [Roborock dashboard](#roborock-dashboard-2026-11-04).
- **Homelab** (`/homelab-hw`) — iDRAC gauges for [[kdk-hyp-01]] and
  [[kdk-nas-01]], the [[nut]] UPS, and CPU/memory/uptime for all monitored hosts.

### Theme tokens after 2026.4

Since the 2026.2/2026.4 Material-Design-3 frontend migration, dialogs, text
fields (`ha-input`), selects/menus, list items and the sidebar popups read
new tokens (`--ha-color-*`, `--md-sys-color-*`, `--md-list-*`,
`--md-ripple-*`, `--wa-color-*`), not the old `mdc-text-field-*` / `input-*`
ones. Two things keep them dark:

- **Dark-only modes.** `kodiak.yaml` is `Kodiak: modes: dark: {...}` with no
  `light` mode. The frontend forces dark mode for such a theme
  (`src/state/themes-mixin.ts`) and lays its own dark defaults under every
  token Kodiak does not set. A flat theme is forced to light mode and lands on
  the light defaults: white fields, orange accent ink.
- **MD3 block.** A delimited block in the theme ("MD3 / 2026.4+ tokens")
  sets form backgrounds, list/menu colours, ripple, selected-item tint and
  dialog/tooltip surfaces in Kodiak colours. Verified against HA 2026.10.4
  (frontend 20260930.5). HA does not support theme variables, so after a
  frontend upgrade grep the bundle (`hass_frontend/frontend_latest/*.js`) for
  each name and fix any that changed.

Secondary text on MD3 surfaces uses **ash-lift `#a2988b`** (5.16:1 on
ink-surface). Plain ash only reaches 3.94:1 there. Links use light-ember
`#d98a58`. After editing the theme, reload it with Developer Tools → YAML →
*Themes* or `frontend.reload_themes`.

## Roborock dashboard (2026-11-04)

`config/dashboards/roborock.yaml`, helpers in `config/packages/roborock.yaml`.

| Section | Shows | Source |
|---|---|---|
| Current status | state + since, error in words with the fix, battery, room (only while cleaning), run phase + time, mode, dock error/activity, tanks, last job | `sensor.rr_vacuum_error`, `input_text.rr_run_phase/near`, dock sensors |
| Rooms | 13 tiles: tap = clean that room now (asks first); state = "2 days ago · Tue Nov 3 09:24"; banner for rooms not cleaned in 2+ days | `sensor.rr_room_<room>_cleaned` (timestamp view of the Node-RED stamps) |
| Mop run | in-progress banner, dock washing/drying, last + next mop pass, clean tank, mode/flow/route, Mop all floors | `rr_last_mop_run`, `rr_next_mop`, dock switches |
| Alerts | every active condition as a coloured banner (error, off charger, dock error, tanks, real water shortage, unfinished run, swap due, parts under 10 h), then a 24 h logbook of the error sensors and the `rr_*` automations | live states |
| Maintenance | Vikunja chores, parts left in hours, tank swap age + Tanks swapped | `sensor.rr_vikunja_chores`, consumable sensors |

Rules the page follows:

- **Polarity.** The dock tank binary sensors are problem class: ON = out, empty
  or full. `binary_sensor.mr_slave_water_shortage_2` only re-evaluates when the
  dock pumps water, so it is shown (and alerted) only while the clean tank has
  not been reseated since it rose.
- **Times** come from the stamps, never from a helper's `last_changed` (every HA
  restart resets it). A stamp means the room's stage *finished*.
- **Room names.** `sensor.mr_slave_current_room_2` and `rr_run_near` carry the
  robot's own room names (Bathroom2 = the dock spot, three rooms are just
  "Bedroom"), not the 13 segment names. Renaming the rooms in the Roborock app
  to the segment names fixes the label (hand step, not done).
- **Alerts** (`rr_stuck_alert`) are titled by error kind: stuck, jam, can't
  charge, dock wash tray, dock water, dirty sensor. `rr_dock_error_alert` pages
  dock errors other than an empty clean tank after 10 min. The off-charger
  alert stays quiet while the robot reports an error (the error alert has
  already paged). Follow-up, not built: the 1-3 s `clean_carousel_*` blips
  (six on 11-03) never page; a repeat counter would catch a failing wash tray.

### Map tab: numbered rooms, key, path colour (2026-11-04)

The Map tab (`/roborock-vac/map`) is a panel view holding a `vertical-stack`:
the `xiaomi-vacuum-map-card` on top, a markdown **Key** below.

- **Numbers.** Each of the 13 `predefined_selections` has `label.text` = its
  segment id ("1".."13"), centred on the room's tap bubble. The Kodiak theme
  carries the card's `map-card-room-*` variables (glyph hidden, ink bubble,
  20 px paper number, ember when selected); the view sets `theme: Kodiak` so
  the tab looks the same whatever theme the browser picked. Tap-to-select is
  unchanged. Theme edits need Developer Tools → YAML → *Themes* reload (or
  "Reload all YAML"); dashboard edits only a browser refresh.
- **Key.** The table `# | Dashboard name | Robot app name | Notes` under the
  map is the only place the number → name mapping is written. A correction
  is a one-row edit there. The robot-app column is "?" until the owner
  matches each number to the app's name (the app still uses the old labels,
  see *Room names* above). Renaming a **helper** (tile, `input_button`,
  Node-RED stamp) is a separate, bigger change: package + Rooms tiles + flows.
- **Renames and fixes.** 2026-11-04: segment 3 "Bedroom 1" → **West
  Bedroom** (display names only: Key, Rooms tile, Home tile, `input_button` /
  `input_datetime` friendly names; entity ids and the `bedroom_1` keys stay).
  Segment 11 (Front Door Entry) marker moved from Rachel's Office onto the
  dark-blue L-shaped room by the front door/stairs (x 18125, y 25300).
- **Calibration** (3 points, mm → px on the 1728 × 1568 image) is unchanged;
  do not re-derive.
- **Path colour.** The image is drawn by HA's Roborock integration
  (`vacuum-map-parser-roborock`) with the library's default palette; the path
  is light blue (147, 194, 238), the same as rooms 2 and 12, so it vanishes on
  them. Neither the integration nor the card has a colour option. The
  integration's options do have **Show rooms**: off makes every room fill
  transparent, so the map shows walls, the dock, the robot and the path on
  the card's dark ink surface, where the light-blue path stands out. The
  numbers and the Key replace the room colours. Hand step (UI, not in git):
  Settings → Devices & services → Roborock → ⚙ Configure → untick
  **Show rooms** → Submit; the integration reloads and the next map update
  redraws. Revert = tick it again. Optional in the same form: **Mop path**
  draws a wide translucent white band where it mopped. Status: not applied
  (owner decision). If the rooms must keep their colours *and* the path must
  show, use the map renderer below (it needs Show rooms back **on**).

### Map renderer (roborock-map sidecar, 2026-11)

`stacks/home-assistant/roborock-map/` — a small Flask service in this stack
that recolours HA's own map PNG into the Kodiak palette. No robot protocol, no
python-roborock: it fetches `image.mr_slave_main_floor_2` through
`/api/image_proxy` and pixel-remaps it (the parser palette is fixed and not
anti-aliased, so every pixel is an exact palette colour).

- **What it draws.** 13 muted Kodiak room fills (distinct per room), carpet
  stipple as a lighter tint of its room, walls ash `#8d8377`, path paper
  `#ece2d0` (ember vanished on the kitchen fill), dock ember, robot paper. Rooms
  2/12 and 8/13 share a parser colour, so rooms are told apart by a seed point
  each (the tap-bubble pixel) and a flood fill, not by colour alone.
- **Glass-door spill.** A polygon in `roborock-map/config.json` (`mask`) is
  cleared to transparent and a `seal` line in wall colour closes the kitchen
  outline. Image pixels, measured on the 1728 × 1568 render.
- **Geometry** is untouched (same size and pixels), so the card's calibration
  points stay valid. The Map tab now reads `image.mr_slave_map_kodiak`.
- **Wiring.** Container `roborock-map`, bridge network, published on
  `127.0.0.1:8125` only; it reaches HA through `host.docker.internal`
  (host-gateway). HA reads it back through a template image in
  `config/packages/roborock.yaml` whose URL carries the source entity's state,
  so it re-fetches exactly when the vacuum's map updates. Not a generic
  camera: generic has no YAML platform.
- **Secret.** `HA_TOKEN` in `/opt/kdk-lab/home-assistant/roborock-map.env`
  (root 600, host-managed; a missing file fails the stack deploy). Source of
  truth: 1Password `kdk-cluster/home-assistant-roborock-map-token`
  (field `credential`), a long-lived token of its own (not the Node-RED one).
- **Needs Show rooms ON** (Settings → Devices & services → Roborock → ⚙
  Configure → tick **Show rooms**). With it off the sidecar still works but
  has no room fills to recolour.
- **Change colours / mask / seeds:** edit `roborock-map/config.json`, PR,
  deploy (Komodo rebuilds the image: `run_build = true`). Preview locally:
  `python render.py in.png out.png --bg '#1b1a18'` with a saved HA map. If
  the robot re-segments the map (new room ids or moved rooms), re-check the
  seeds against the card's `predefined_selections` pixel positions.
- **Check:** `ssh kdkadmin@10.1.30.21 'curl -s 127.0.0.1:8125/healthz'`
  (`last_error` null, `rendered_at` recent once the tab was viewed).
- **Roll back:** point the Map tab back at `image.mr_slave_main_floor_2`
  (the original entity is untouched).

### Mop runs, dock visits and recharge pauses (2026-11-04)

Learned from the robot's own "Mopping" routine on 11-04 (5 washes, one
low-battery pause). The robot's routines leave `select.mr_slave_cleaning_mode`
at `vac_and_mop` and do not touch `rr_run_phase`, so the page reads the job
from the robot through four package helpers:

| Helper | On / value when | Why |
|---|---|---|
| `binary_sensor.rr_job_active` | `cleaning_2` on, or status is a cleaning or dock-visit status (going to wash, washing, emptying, returning); 2 min `delay_off` | `cleaning_2` drops off for ~40-70 s at every wash/empty visit; tiles keyed on it flickered |
| `binary_sensor.rr_recharge_pause` | status `charging`/`charging_complete` with `cleaning_2` still on and progress > 0, for 1 min | Pauses at ≤ 20 %, resumes by itself at 80 %; shown as "Paused to recharge · X % · resumes at 80 %", never as idle or finished |
| `sensor.rr_job_washes` | +1 each time status turns `washing_the_mop`; 0 when cleaning starts after ≥ 20 min off | Wash count for the current job. Not reset on `last_clean_begin_2`: that sensor updates when a job **ends** |
| `binary_sensor.rr_mopping_now` | job active and (dock washing, or washed this job, or cleaning with mop mode + flow + pads and water box attached) | Washes are the proof; pads are always attached on this robot, so they count only with the mop settings |

- An app/HomeKit job that is vacuum-only but leaves the select at
  `vac_and_mop` shows as mopping until it ends; a wash count of 0 is the hint.
- Node-RED (`f_track`): when the robot starts cleaning outside a daily run or
  mop pass, the phase becomes "Robot job started outside the daily run", so
  an old "Stopped by hand" no longer sits on the page. `error → idle` (an error
  being cleared) is no longer logged as a manual stop.
- `rr_off_charger_alert` only runs on docking when an off-charger alert is out
  (`input_boolean.rr_off_charger_alerted`), so wash visits and the recharge
  pause no longer show as triggers in the logbook.

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

### Restarts and failures (2026-11-02)

The daily-run flow is hardened against Home Assistant / Node-RED restarts and
reports a failed mop:

- **HA restart / reconnect.** The `unavailable → X` flap no longer fires the
  state-change triggers (`t_idle`, `t_86`, `t_dock`, `t_croom` ignore
  unavailable/unknown previous states), so a reconnect can't cancel a run or
  falsely complete the "Fill water" chore. The dock-waits
  (`wait dock (A)/(B)/(rest)/(mop)`) only see a state *change*, so a dock that
  happens during a reconnect would otherwise hang them to timeout; a
  `home_assistant_client` **ready/states-loaded** handler (`ev_hareconn` →
  `f_hareconn`) checks, if a run is active and the robot is already
  docked/charging, which wait is pending and advances it the same way the
  wait's success output would. Each dock-wait is armed with a generation token
  (`arm_*` nodes); a guard on both of its outputs drops a stale firing, so the
  wait that was bypassed on reconnect can't later double-advance the run or trip
  its timeout into a spurious cancel.
- **Node-RED restart.** `settings.js` now has a `contextStorage` **file** store
  (localfilesystem under `/data/context`); the run tracker `rr_run` and the
  water-refill throttle `vik_fill_last` are written there, so they survive a
  restart (the rest of the run flags stay in memory by design — a restart ends
  the run anyway). A startup check (`i_boot` → `f_boot`, ~30 s after start)
  finds an interrupted run: if the robot is no longer cleaning it marks the run
  interrupted with **no room stamps** and pushes a *"Run interrupted by a
  restart — tap Daily clean to resume"* alert. **The file store only exists
  once `settings.js` is deployed, so deploy the stack before importing a
  `flows.json` that uses it (see below).**
- **Mop day and catch-up (2026-11-04).** One rule, `rrMopDue`, sits byte-identical
  in `f_35`, `f_pre`, `f_58` (the mop gate) and `f_nx` (`rr_next_mop`); the tests
  fail if the copies drift. One clean tank lasts one mop pass, so at most one a week:

  | Day | Mop? |
  |---|---|
  | Wednesday | yes, unless `input_datetime.rr_last_mop_run` is within the last 48 h |
  | Thursday, Friday | catch-up only if no mop since this week's Monday 00:00 (Wednesday was missed) and none in 48 h |
  | Saturday to Tuesday | never (11-03: a Tuesday catch-up emptied the tank the day before the Wednesday mop) |

  The start-of-run water/dock gate (`f_pre` → `rr_mop_skip`) still applies on
  every mop day. The 07:45 "needs clean water" nag fires when `rr_next_mop` is
  today, so it follows a catch-up day too.
- **Mop-pass end and failures.** HA exposes no routine-step entity, so `f_mopchk`
  calls the 7-job "Mopping" routine done only after cleaning has been off for
  **20 min** with the robot home (docked/charging) and not washing; gaps between
  routine steps (8.5 min on 11-03) no longer count as the end. A mid-pass water
  watch (`t_mopwater` → `f_mopwater`) fires when
  `binary_sensor.mr_slave_water_shortage_2` turns on during a pass. Any mop that
  does not complete (water ran out, dock/battery/routine timeout, give-up, or a
  start-time skip) sends **one** urgent `rr_mop_failed` alert (`f_mopfail`) and
  does **not** stamp `rr_last_mop_run`, so the next eligible day re-runs it.
- **Daily run stages** (since 2026-11-05). Each stage is its own robot job; the
  flow waits for the dock (`sensor.mr_slave_status_2` = `charging`) before the
  next, and a wait that runs out is a stall (alert, rest of run cancelled).

  | # | Stage (`input_text.rr_run_phase`) | Robot job | Dock wait | Stamps |
  |---|---|---|---|---|
  | 1 | Master bedroom — zone A | `app_zoned_clean` | 40 min | — |
  | 2 | Master bedroom — zone B | `app_zoned_clean` | 40 min | master bedroom |
  | 3 | Main floor sweep (11 rooms) | `app_segment_clean` [1,4,5,3,13,12,11,6,7,8,10] | 150 min (+ recharge watcher) | the 11 rooms |
  | 4 | Rachel's office (last) | `app_segment_clean` [9] | 40 min | Rachel's office |
  | 5 | Mop pass (all floors), mop days only | `button.mr_slave_mopping` routine | 120 min | `rr_last_mop_run` |

  Between stages the dock empties the bin (8 s settle, best-effort
  `app_start_collect_dust`, 50 s) and the run checks `rr_cancel`. The robot
  ignores the order of a segment list: on 11-05 it was handed all 12 segments
  in one call, started in Rachel's office, hit `nogo_zone_detected` after
  25 min and the whole sweep died. The office now goes only after the 11-room
  job docked normally. An office job back in under 2 min is not stamped
  (`rr_short_zone` notice, the run moves on). A short sweep or a failed
  recharge-resume skips the office too (straight to the mop gate). The office
  dock wait has its own HA-reconnect recovery (`f_hareconn` out 4).
- **Main sweep sanity.** `f_rc` does not stamp the 11 rooms if the sweep came
  home in under 10 min with progress 0 (11-03: 1.7 min); the run moves on and an
  `rr_sweep_short` notice goes out. A mid-sweep recharge that fails to
  resume (progress reset to 0, or charged & full for ~10 min, or 150 min cap) is
  detected by the recharge watcher (`d_rcpoll` → `f_rcpoll`) and routed to the
  mop gate **without** stamping the unfinished rooms, with an `rr_no_resume`
  alert (it no longer waits out a 240-min timeout and then stamps everything).

Function-node logic has unit tests: `node stacks/home-assistant/node-red/tests/flow-logic.test.js`.

After a flow change is merged, push it to the live board and check it took
(`import` backs up the live flow and restarts node-red):

**On the laptop as jmack, in the kdk-lab-dkr checkout on `main`** (the script
SSHes to kdk-dkr-02 as kdkadmin, `ssh kdkadmin@10.1.30.21`)

```sh
git pull --ff-only
node stacks/home-assistant/node-red/tests/flow-logic.test.js
scripts/nodered-flow-sync.sh import
scripts/nodered-flow-sync.sh diff      # "repo matches live"
```

Then watch the next scheduled run's `input_text.rr_run_phase` (dashboard
Current status, or the HA logbook for that entity): zone A, zone B, emptying
before main sweep, main floor sweep (11 rooms), emptying before Rachel's
office, Rachel's office (last), then main sweep complete and mop pass or
complete.

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
  package edits reload with Developer Tools → "Reload all YAML" (a *new*
  package that adds integrations needs a one-time restart).
- **Workout pages** — `config/packages/workout_strength*.yaml` and
  `config/dashboards/workout*.yaml` are **generated** by
  `tools/gen_workout.py` from `tools/workout_catalog.json` (one list, both
  people; CI checks they are current). Add/rename/remove an exercise with the
  [workout-tracking-update runbook](../runbooks/workout-tracking-update.md).
- **Approval tasks** — `config/packages/approval_tasks.yaml` is a generic
  decision loop: a Vikunja task in the Approvals project raises an actionable
  notification (polled, no inbound path, no container), and Approve/Skip (or
  task-declared choices) is recorded back on the task. Token/project/label ids
  live in the host `secrets.yaml` (`vikunja_approvals_*`). See the
  [approval-tasks runbook](../runbooks/approval-tasks.md).
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
  Vikunja bridge; token in the "vik auth" function node). The flow itself is
  **repo-owned** (`stacks/home-assistant/node-red/flows.json`); push a change to the
  live board with `scripts/nodered-flow-sync.sh import` (secrets stay in the
  host-side `flows_cred.json`, never exported). `settings.js` is also
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
`home-assistant-roborock-map-token` (roborock-map → HA, read-only use: the
image proxy): create a new long-lived token on the HA profile page, `op item
edit`, write `HA_TOKEN=` into `roborock-map.env`, recreate `roborock-map`,
then delete the old token in HA.
