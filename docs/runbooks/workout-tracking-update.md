---
title: Exercise plan update (wger)
---

# Exercise plan update (wger)

[[wger]] is the household's only exercise tracker since **2026-11-06** (the owner chose it
in the tracking pilot). This page is how a change to the plan (an exercise, a dose, a cue,
a picture) reaches both people's wger routines, and, at the end, how the in-house tracker
was retired.

| Where | What | Made by |
|---|---|---|
| The private vault | the week's order, Jacob's doses and cues, the pictures (`3. Resources/Exercise/`) | the vault's own generator (`ex_days.py`), not in this repo |
| `stacks/home-assistant/tools/workout_catalog.json` | the people, the slugs, each item's kind, and Rachel's own plan (`plans.rach`: days, doses, cues) | hand-edited; loader + checks in `workout_catalog.py` |
| `stacks/home-assistant/tools/wger_exercises.json` | per slug: wger muscles, equipment, optional what/safety lines | hand-edited |
| Laptop scratch dir (default `/tmp/workout-guide`) | `workout.json` (Jacob's days, steps, doses, cues) + `exercise/` posters | `tools/gen-workout-guide.py` from the vault; **never committed, never published** |
| wger | one custom exercise per slug (images, muscles, description) + each person's seven weekday routines (Mon · … .. Sun · …) | `tools/gen_wger.py` from the catalog, the sidecar and the scratch dir |

Nothing in Home Assistant or on home.kmkdp.com follows the plan any more: the HA
**Workout** dashboards are the Treadmill view only, and treadmill walks reach wger through
`treadmill_wger.yaml` ([wger runbook step 14](wger.md#14-treadmill-auto-log)).

**Why the guide generator stays.** `gen_wger.py --posters DIR` reads Jacob's week (doses,
cues, step order) and every poster from the guide output. Keeping
`gen-workout-guide.py` as a laptop-only build step is the least churn: `gen_wger.py` and
its tests are unchanged, Jacob's health content stays in the vault (one source, no second
copy in this public repo), and the posters are vault files that could not live in the
catalog anyway. It moved next to its only consumer and writes to a scratch dir instead of
the published guide.

## Per-person plans

The top-level `groups`/`items` are the first person's slugs (Jacob); his doses and cues
come from the guide output. A person can have their **own** plan under
`"plans": {"<login>": {...}}` (Rachel: `plans.rach`) that carries what the guide would
otherwise supply:

```json
{"id": "high_box_squat", "name": "High Box Squat", "group": "strength_a", "kind": "w",
 "days": ["mon"], "slugs": ["high-box-squat"],
 "steps": [{"slug": "high-box-squat", "title": "High Box Squat",
            "dose": "3 × 8 · RPE 6–7 · rest 60–90 s", "cues": ["…", "…"]}]}
```

- `days`: `["daily"]` or weekday codes (`mon` … `sun`); `steps`: one per slug, in the same
  order as `slugs`; `"either": true` on a two-slug item = one wger slot holding both
  ("Either one: log the one you did"). The loader (`workout_catalog.py`) checks all of it.
- **Public repo:** exercise names, doses, rest and movement cues only. Group names stay
  neutral; the reasons for a plan live in the vault (`3. Resources/Exercise/<person>/`).
- Exercises are global in wger, so a slug may appear in **one** person's plan only (an
  error otherwise: a wger exercise carries one dose text). Give the other person their
  own slug and sidecar line.
- `id` is lowercase `[a-z0-9_]`, unique per list; `group` must exist in that list's `groups`.

## Kinds

| `kind` | wger unit |
|---|---|
| `w` | weight (lb) + Repetitions |
| `r` | Repetitions |
| `t` | Seconds |
| `wt` | weight + Seconds |
| `wl` | weight + `10-ft lengths` (carries, e.g. `suitcase`) |
| `min` | Minutes |
| `done` | a routine (hand routine, flexibility flow, stretches): its slugs get the unit the dose names, else Repetitions |
| `score` | a 0–10 check-in with no slugs (`knee_pain`, `hand_pain`): nothing in wger; pain scores stay in the vault Daily note |

A dose whose unit wger does not have yet makes `gen_wger.py --apply` print an ADMIN STEP
and stop before the routines (units are admin-only; [wger runbook](wger.md)).

## Procedure

1. **Vault first (laptop).** Change the exercise in the vault generator, regenerate, and
   look at the new pictures. If the vault Daily template logs the item, update it there.
2. **Edit the catalog** only when a slug is added, removed or renamed, or Rachel's plan
   changes (a Jacob dose or cue change needs no repo edit: go to step 4):
   - add: `{"id": "goblet_squat", "slugs": ["goblet-squat"], "name": "Goblet Squat", "group": "lower_a", "kind": "w"}`
     plus its `wger_exercises.json` line (muscles, equipment);
   - rename the display name: change `name`; the wger exercise keeps its id and logs;
   - remove: delete the entry (wger keeps a slot that has logs, see step 5).
3. **Test, PR, merge.**
   **Laptop, repo root**
   ```bash
   python3 -m pytest -q stacks/home-assistant/tools/tests
   ```
   CI (`workout-generated`) runs the same suite. Nothing deploys: wger is loaded from the
   laptop in step 5.
4. **Build the guide output** into the scratch dir:
   **Laptop, repo root**
   ```bash
   python3 stacks/home-assistant/tools/gen-workout-guide.py --out /tmp/workout-guide
   ```
   It prints `cues for N day steps`; a `WARNING: ... no cues` line means it could not
   import the vault's `ex_days.py` (fix that before loading wger, or the descriptions lose
   their cues). Per-step photos (`ex-<slug>-step<N>.png`) are copied into
   `/tmp/workout-guide/exercise/` **after** this run (a run starts `exercise/` clean).
5. **Load the change into wger** (full notes in
   [wger runbook step 9](wger.md#9-load-the-catalog-our-exercises--weekly-routines)):
   **Laptop, repo root, as jmack (`op` signed in), after the PR is merged**
   ```bash
   G=/tmp/workout-guide
   export WGER_URL=https://wger.kmkdp.com
   export WGER_API_KEY_JMACK="$(op read op://kdk-cluster/wger-api-jmack/credential)"
   export WGER_API_KEY_RACH="$(op read op://kdk-cluster/wger-api-rach/credential)"
   python3 stacks/home-assistant/tools/gen_wger.py --posters "$G"            # dry run: read the planned writes
   python3 stacks/home-assistant/tools/gen_wger.py --posters "$G" --apply
   python3 stacks/home-assistant/tools/gen_wger.py --posters "$G" --check    # "wger matches the catalog"
   unset WGER_API_KEY_JMACK WGER_API_KEY_RACH
   ```
   A removed exercise's slot is deleted from the routine unless someone logged it; then
   it moves to the end of the day as `[retired]` (deleting it would delete the logs).
   Delete it in the app once the history no longer matters.
6. **Verify** on both phones: wger app → Routines → the weekday's routine (e.g. **Mon · Lower A**) → the changed day shows
   the change (pull to refresh; the app syncs through PowerSync).

**Quarterly:** wger caps a routine at 120 days. `gen_wger.py --check` fails (exit 1) once
the end is less than 28 days away; `--apply` then rolls the window forward (vault task,
tag `#homelab/wger`).

## Rollback

Revert the catalog PR, re-run steps 4 and 5 from the reverted vault state: wger follows
(slots with logs are kept as `[retired]`, nothing logged is lost).

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
treadmill (`treadmill.yaml`, `treadmill_wger.yaml`, the Treadmill view on both Workout
dashboards and the sidebar links to them), the wger stack, `gen_wger.py`, both catalog
files.

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
[Treadmill bridge](treadmill-glassos-bridge.md)
