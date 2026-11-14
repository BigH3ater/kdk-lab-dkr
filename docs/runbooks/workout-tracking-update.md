---
title: Exercise plan update (wger)
updated: 2026-11-14
---

# Exercise plan update (wger)

[[wger]] is the household's only exercise tracker since **2026-11-06**. This page is how a
change to the plan (an exercise, a dose, a weight, a cue, a video) reaches both people's
wger routines, and, at the end, how the in-house tracker was retired.

Since **2026-11-14** one file holds the whole plan for both people and the routines use
wger's built-in exercises only (owner decision; the 71 custom `kdk-lab` exercises, their
images and the old routines are removed by `gen_wger.py --purge-kdk`, [wger runbook step
9b](wger.md#9b-purge-the-old-kdk-data-once-owner-present)). The vault day files, the guide
generator (`gen-workout-guide.py`), `workout_catalog.json` and `wger_exercises.json` are
retired.

| Where | What | Made by |
|---|---|---|
| `stacks/home-assistant/tools/wger_plan.json` | the built-ins used (uuid, expected name, How / Safety / video) and each person's routines (weekday, slots, sets, reps, start weight, rest) | hand-edited; checked by `gen_wger.py` and its tests |
| wger | the built-ins' English descriptions + jmack's four and rach's seven weekday routines | `tools/gen_wger.py --apply` from the laptop |
| The private vault | the reasons (knee rules, history, Rachel's two non-wger items) | by hand; never in this public repo |

**Public repo:** exercise names, sets, reps, start weights, rest and movement cues only (the
owner's 2026-11-14 decision). No pain notes, no medical history.

## The plan file

```json
"exercises": {
  "goblet-squat": {"uuid": "b7c6a444-…", "name": "Dumbbell Goblet Squat",
                   "how": ["…", "…", "…"], "safety": "…",
                   "video": "https://www.youtube.com/watch?v=…", "equipment": ["…"]}},
"people": {"jmack": {"name": "Jacob", "about": "<routine description: progression rule>",
  "parts": {"<name>": [<slots>]},
  "routines": [{"day": "mon", "name": "Mon · Lower A", "summary": "…", "slots": [
    {"exercise": "goblet-squat", "sets": 3, "reps": [8, 12], "weight": 15, "rest": [60, 90],
     "comment": "One dumbbell; squat to the bench"},
    {"exercise": "step-up", "sets": 3, "reps": [8, 10], "per_side": true},
    {"part": "<name>"}]}]}}
```

- **Exercise key** (`goblet-squat`) is only a name inside the file. `uuid` picks the
  built-in (names are not unique in wger: two "Side Plank", two "Single Leg RDL"), `name` is
  the English name wger reports for it; the run stops when they disagree. Find a uuid with
  `curl -s https://wger.kmkdp.com/api/v2/exerciseinfo/<id>/ | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["uuid"], [t["name"] for t in d["translations"] if t["language"] == 2])'`.
- `how` 3-5 short lines, `safety` one line, `video` a `https://www.youtube.com/watch?v=<id>`
  address from a physical therapist or certified coach channel (verify with
  `--check-videos`). They become the description of the built-in, which **everyone** using
  that exercise sees; a person-specific note goes in the slot `comment` (160 characters).
  `equipment` names are added to the exercise (never removed).
- A slot has exactly one of `reps`, `seconds`, `minutes`, `lengths` (10-ft lengths), each a
  number or `[min, max]`; `rest` the same in seconds; `weight` in lb (per dumbbell unless
  the comment says one dumbbell); `per_side: true` doubles the sets (one logged set per
  side). `parts` are reusable slot lists (Rachel's daily desk and evening parts).
- Every exercise must be used by some routine; routine names fit wger's 25 characters.
- Address on the `Video:` line: `video_url()` in `gen_wger.py` (the YouTube address today).

## Procedure

1. **Edit `wger_plan.json`** (a new exercise: pick the built-in in the web UI or with
   `exerciseinfo/?uuid=`, add its key, uuid, name, cues and a video; then use it in a slot).
2. **Test, PR, merge.**
   **Laptop, repo root**
   ```bash
   python3 -m pytest -q stacks/home-assistant/tools/tests
   python3 stacks/home-assistant/tools/gen_wger.py --check-videos     # every video answers YouTube oEmbed
   ```
   CI (`workout-generated`) runs the tests. Nothing deploys: wger is loaded from the laptop.
3. **Load the change into wger** ([wger runbook step 9](wger.md#9-load-the-plan-built-in-exercises--weekday-routines)):
   **Laptop, repo root, as jmack (`op` signed in), after the PR is merged**
   ```bash
   export WGER_URL=https://wger.kmkdp.com
   export WGER_API_KEY_JMACK="$(op read op://kdk-cluster/wger-api-jmack/credential)"
   export WGER_API_KEY_RACH="$(op read op://kdk-cluster/wger-api-rach/credential)"
   python3 stacks/home-assistant/tools/gen_wger.py             # dry run: read the planned writes
   python3 stacks/home-assistant/tools/gen_wger.py --apply
   python3 stacks/home-assistant/tools/gen_wger.py --check     # "wger matches the plan"
   unset WGER_API_KEY_JMACK WGER_API_KEY_RACH
   ```
   A removed exercise's slot is deleted from the routine unless someone logged it; then it
   moves to the end of the day as `[retired]`.
4. **Verify** on both phones: wger app → Routines → the weekday's routine → pull to refresh.

**Progression** (jmack, in each routine's description): when every set reaches the top of
the rep range, go up one dumbbell step and start again at the bottom of the range. Change
the slot's `weight` (and `reps`) in the plan and re-run step 3 when that happens, or log the
heavier weight in the app and update the plan at the next change.

**Quarterly:** wger caps a routine at 120 days. `--check` fails (exit 1) once the end is
less than 28 days away; `--apply` then rolls the window forward (vault task, tag
`#homelab/wger`).

## Rollback

Revert the plan PR and re-run step 3: wger follows (slots with logs stay as `[retired]`,
nothing logged is lost).

## Retirement of the in-house tracker (2026-11-06)

Removed in the repo (one PR): the `/workout/` Today page (`stacks/dashboard/workout/`, the
`dashboard-workout` service, its Traefik router and HA token), the published guide
(`/guides/workout/`, its nginx location, the Guides tile and the host mount), the HA
per-exercise helpers for both people (`workout_strength.yaml`,
`workout_strength_rachel.yaml`: 132 `input_number.ex_*` / `input_datetime.ex_*` /
`script.workout_mark_done` registry entries on 2026-11-06) with the Strength / Progress /
Plan dashboard views, `gen_workout.py` and its CI step, and the `dashboard-workout`
firewall exception. The owner accepted losing the helpers' recorder history; the
`/workout/` CSV log never received a row (directory empty on 2026-11-06). Kept: everything
treadmill (`treadmill.yaml`, the Treadmill view on both Workout
dashboards and the sidebar links to them; all retired 2026-11-08, see
[the retired treadmill page](treadmill-glassos-bridge.md)), the wger stack, `gen_wger.py`,
both catalog files (retired 2026-11-14 for `wger_plan.json`).

Host steps, in order, after the PR is merged. The main session runs the checks and
watches the deploys; the deletes, the token and the 1Password archive are the owner's.

1. **Dashboard deploy (kdk-dkr-01).** The next gitops-sync (`:00/:15/:30/:45`, or run
   `gitops-sync` in Komodo) applies the new `pre_deploy` from `komodo/resources.toml` and
   redeploys `dashboard`; `dashboard-guides` is recreated (the nginx conf hash changed).
   Compose does not remove a service that left the file, so remove the orphan by hand:
   **kdk-dkr-01, as kdkadmin**
   ```bash
   sudo docker ps -a --filter name=dashboard-workout --format '{{.Names}} {{.Status}}'   # still listed = orphan
   sudo docker rm -f dashboard-workout
   sudo docker image rm kdk/dashboard-workout:latest
   sudo docker ps --filter name=dashboard-guides --format '{{.Names}} {{.Status}}'      # Up (recent)
   ```
   Check from a logged-in browser: `https://home.kmkdp.com/workout/` is no longer the
   Today page (Homepage answers it), `https://home.kmkdp.com/guides/workout/workout.html`
   is nginx's 404, and the Guides page has no Workout tile.
2. **Home Assistant deploy (kdk-dkr-02).** The same sync redeploys `home-assistant`; its
   `pre_deploy` copies the two Treadmill-only dashboards. `cp -f` never deletes, so the two
   retired packages are still on the host and still loaded. Remove them, check, reload:
   **kdk-dkr-02, as kdkadmin**
   ```bash
   C=/opt/kdk-lab/home-assistant/config
   sudo grep -c 'title: Strength' $C/dashboards/workout.yaml $C/dashboards/workout_rachel.yaml   # 0 and 0 = new files landed
   sudo rm $C/packages/workout_strength.yaml $C/packages/workout_strength_rachel.yaml
   sudo docker exec homeassistant python -m homeassistant --script check_config -c /config 2>&1 | tail -5
   ```
   Then reload: Developer tools → YAML → **All YAML configuration**, or:
   **Laptop (`op` signed in)**
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     -H "Authorization: Bearer $(op read op://kdk-cluster/homepage-ha-token/credential)" \
     https://homeassistant.kmkdp.com/api/services/homeassistant/reload_all     # 200
   ```
   No restart: `input_number`, `input_datetime` and `script` reload in place.
3. **Entity clean-up.** After the reload the YAML helpers are gone from the state machine.
   Check the entity registry for leftovers (read-only):
   **kdk-dkr-02, as kdkadmin**
   ```bash
   sudo cat /opt/kdk-lab/home-assistant/config/.storage/core.entity_registry | python3 -c '
   import json, sys
   e = json.load(sys.stdin)["data"]["entities"]
   left = [x["entity_id"] for x in e if x["entity_id"].split(".")[1].startswith("ex_") or x["entity_id"] == "script.workout_mark_done"]
   print(len(left), left[:5])'                                     # 132 before the reload; want 0
   ```
   If any are left (shown as "Not provided" / read-only in the UI): Settings → Devices &
   services → **Entities** → search `ex_` → select all → **Remove selected**; then search
   `workout_mark_done` the same way. Optional, to drop their history now instead of after
   the 10-day recorder window: Developer tools → Actions → `recorder.purge_entities` with
   `entity_globs: [input_number.ex_*, input_datetime.ex_*]`,
   `entity_id: script.workout_mark_done`, `keep_days: 0`.
4. **Confirm nothing references the removed entities.**
   **Laptop, repo root** (expect no output)
   ```bash
   grep -rnE '(input_number|input_datetime)\.ex_|workout_mark_done|dashboard-workout|/guides/workout|home\.kmkdp\.com/workout' \
     stacks/home-assistant/config stacks/dashboard stacks/loki stacks/monitoring komodo hosts
   ```
   **kdk-dkr-02, as kdkadmin** (expect no output; on 2026-11-06, before the removal, no UI
   automation, script, storage dashboard or Node-RED flow used them)
   ```bash
   C=/opt/kdk-lab/home-assistant
   sudo grep -rlE '(input_number|input_datetime)\.ex_|workout_mark_done' $C/config --include='*.yaml' $C/node-red 2>/dev/null
   sudo grep -lE '(input_number|input_datetime)\.ex_|workout_mark_done' $C/config/.storage/lovelace* 2>/dev/null
   sudo docker logs --since 10m homeassistant 2>&1 | grep -iE 'ex_|workout_mark_done|invalid config' | tail
   ```
   Docs: `grep -rn 'gen_workout\|/workout/' docs` lists only this section, the dashboard
   page's "Retired" note and the service pages' one-line history.
5. **Owner deletes (kdk-dkr-01).** The guide copy (every picture in it is also in the
   vault, checked 2026-11-06) and the empty log dir. **After** step 1 (the old
   `pre_deploy` recreated both dirs on every deploy):
   **kdk-dkr-01, as kdkadmin**
   ```bash
   sudo ls -A /opt/kdk-lab/dashboard/workout-log                  # empty: nothing to archive
   sudo rm -rf /opt/kdk-lab/dashboard/workout /opt/kdk-lab/dashboard/workout-log
   ```
6. **Owner: revoke the sidecar's HA token.** HA → Profile → Security → Long-lived access
   tokens → delete the token made for the workout sidecar; then archive its 1Password item
   (nothing reads it any more: `.env.tpl` dropped the reference):
   **Laptop (`op` signed in)**
   ```bash
   op item delete home-assistant-workout-token --vault kdk-cluster --archive
   ```
7. **Firewall exception (any time; harmless if late).** The repo's dkr-01 policy no
   longer has `allow_ctr dashboard-workout`; an absent container already matches nothing
   (the script fails closed).
   **kdk-dkr-01, as kdkadmin, in a clone of the merged repo**
   ```bash
   sudo install -m 644 hosts/kdk-dkr-01/kdk-docker-user.conf /etc/default/kdk-docker-user
   grep -c dashboard-workout /etc/default/kdk-docker-user                          # 0
   sudo systemctl start kdk-docker-user.service && journalctl -u kdk-docker-user -n 2 --no-pager
   ```

**Rollback of the retirement:** revert the PR. The next sync rebuilds `dashboard-workout`
(it needs the `home-assistant-workout-token` item: restore it from the 1Password archive
first, or `op inject` fails and the dashboard stack does not deploy) and copies the two
packages back to kdk-dkr-02 (reload all YAML: the helpers return empty). Rebuild the guide
with `gen-workout-guide.py --out` and copy it to `/opt/kdk-lab/dashboard/workout`
(`scp` + `sudo cp -rT`).

Related: [wger](../services/wger.md) · [wger runbook](wger.md) ·
[Home Assistant](../services/home-assistant.md) · [Dashboard](../services/dashboard.md) ·
[Treadmill console HUD](treadmill-hud.md)
