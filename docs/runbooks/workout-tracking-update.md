---
title: Workout tracking — add, rename or remove an exercise
---

# Workout tracking update

The workout log lives in four places that must agree:

| Where | What | Made by |
|---|---|---|
| Home Assistant | one helper per logged value, per person (`input_number.ex_<id>_weight`, `…_reps`, `…_seconds`, `…_minutes`, `…_score`, `input_datetime.ex_<id>_done`; Rachel's end in `_rachel`) + the Strength / Progress views of `workout.yaml` and `workout_rachel.yaml` | `stacks/home-assistant/tools/gen_workout.py` from **`tools/workout_catalog.json`** |
| home.kmkdp.com | the Workout guide (`/guides/workout/workout.html`) and the `/workout/` Today page (poster, dose, cues and a per-set form per exercise; writes a CSV and the HA helpers) | `stacks/dashboard/guides/gen-workout-guide.py` on the laptop, from the private vault; output is host-managed in `/opt/kdk-lab/dashboard/workout` |
| wger (pilot Track A) | one custom exercise per slug (poster as image) + each person's **Week (kdk)** routine, weekday-pinned, with the guide's doses | `stacks/home-assistant/tools/gen_wger.py` from the catalog **and** the guide output (`workout.json` + posters) |
| The private vault | the week's order, doses, pictures, the Daily-note log fields | the vault's own generator (`ex_days.py`) — not in this repo |

**One list per side.** The repo side is the catalog; never hand-edit
`config/packages/workout_strength*.yaml` or `config/dashboards/workout*.yaml`
(CI fails if they differ from the generator output). The vault side is the
vault generator. The link between the two is the **slug**: every catalog
item lists the vault picture slugs (`ex-<slug>.svg`) it logs for.

## Kinds

| `kind` | Logs | Helpers |
|---|---|---|
| `w` | weight + reps | `_weight` (lb, step 0.5), `_reps` |
| `r` | reps only (body weight) | `_reps` |
| `t` | seconds only (holds) | `_seconds` |
| `wt` | weight + seconds (holds) | `_weight`, `_seconds` |
| `wl` | weight + 10-ft lengths (carries, e.g. `suitcase`) | `_weight`, `_lengths` (0–20, step 1) |
| `min` | minutes | `_minutes` |
| `score` | a 0–10 rating | `_score` |
| `done` | "done at" time for a routine | `input_datetime.ex_<id>_done` + the shared `script.workout_mark_done` |

## Procedure

1. **Vault first (laptop).** Change the exercise in the vault generator,
   regenerate, and look at the new pictures. If the item is logged, add or
   rename its Daily-note log key in the vault's Exercise Log catalog and the
   Daily template, and run the template render test with node. Write the
   matching range-of-motion image prompt if the item is new.
2. **Edit the catalog** `stacks/home-assistant/tools/workout_catalog.json`:
   - add: `{"id": "goblet_squat", "slugs": ["goblet-squat"], "name": "Goblet Squat", "group": "lower_a", "kind": "w"}`.
     `id` is lowercase `[a-z0-9_]`, unique; `group` must exist in `groups`.
   - rename the **display name**: change `name` only — entity ids stay, history stays.
   - rename the **id** or change the **kind**: this creates new helpers and the
     old ones disappear (the recorder keeps only 10 days anyway; the CSV keeps
     everything, keyed by `id`). List the old and new helper ids in the PR body.
     The CSV is the durable record: old rows keep their columns (a kind change
     to `wl` leaves the old `seconds`/`reps` values in place; new rows fill
     `lengths`). Nothing in HA needs migrating.
   - remove: delete the entry.
3. **Regenerate and test:**
   **Laptop, repo root**
   ```bash
   python3 stacks/home-assistant/tools/gen_workout.py
   python3 -m pytest -q stacks/home-assistant/tools/tests stacks/dashboard/tests
   ```
   Optional full HA schema check (needs Python 3.14; downloads HA once):
   **Laptop, repo root**
   ```bash
   d=$(mktemp -d); mkdir $d/packages; cp stacks/home-assistant/config/packages/workout_strength*.yaml $d/packages/
   printf 'homeassistant:\n  packages: !include_dir_named packages\n' > $d/configuration.yaml
   uvx --python 3.14 --from homeassistant==<HA version from compose.yaml> hass --script check_config -c $d
   ```
4. **PR → review → merge.** The Komodo `home-assistant` deploy's `pre_deploy`
   copies `config/packages/*.yaml` and `config/dashboards/*.yaml` to
   `/opt/kdk-lab/home-assistant/config/` on kdk-dkr-02. Nothing reloads by
   itself.
5. **Reload HA:** Developer tools → YAML → **All YAML configuration**, or
   **Laptop (`op` signed in)**
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     -H "Authorization: Bearer $(op read op://kdk-cluster/homepage-ha-token/credential)" \
     https://homeassistant.kmkdp.com/api/services/homeassistant/reload_all
   ```
   (any admin token works; never echo it). `input_number`, `input_datetime`
   and `script` all reload in place — no restart. Dashboard YAML is picked up on
   a browser refresh.
6. **Regenerate the guide on the laptop** and copy it to kdk-dkr-01 (the
   commands are in the `gen-workout-guide.py` docstring):
   ```bash
   python3 stacks/dashboard/guides/gen-workout-guide.py --out /tmp/workout-guide
   scp -r /tmp/workout-guide/. kdkadmin@10.1.20.20:/tmp/workout
   ssh kdkadmin@10.1.20.20 'sudo install -d -m 755 /opt/kdk-lab/dashboard/workout \
     && sudo cp -rT /tmp/workout /opt/kdk-lab/dashboard/workout && rm -rf /tmp/workout'
   ```
   This also refreshes `workout.json`, which the `/workout/` page reads on the
   next request. A catalog change reaches `/workout/` with the dashboard stack
   deploy (it reads the catalog from the repo clone).
   **Also re-run this step whenever only the cues or the doses change** in the
   vault (no catalog edit): `workout.json` (version 2) carries each step's `cues`
   (read from the vault card generator `scripts/ex_days.py`) and `sets` (the
   number before "×" in the dose), and the Today page sizes its set rows from
   it. The generator prints `cues for N day steps`; a `WARNING: ... no cues`
   line means it could not import `ex_days.py` (the guide is still written, the
   cards just have no cue list). The page accepts version 1 files too (no cues,
   set count from the dose).

   **Then load the change into wger** (both routines + the exercises, from the same
   `/tmp/workout-guide`; full notes in [wger runbook step 9](wger.md#9-load-the-catalog-our-exercises--weekly-routines)):
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
   A removed exercise's slot is deleted from the routine unless someone logged it;
   then it moves to the end of the day as `[retired]` (deleting it would delete the
   logs). Delete it in the app once the history no longer matters. A new kind that
   needs a new unit prints an ADMIN STEP and stops before the routines.
7. **Verify** (both people):
   **Laptop (`op` signed in)**
   ```bash
   TOKEN=$(op read op://kdk-cluster/homepage-ha-token/credential)
   for e in input_number.ex_<id>_reps input_number.ex_<id>_reps_rachel; do
     curl -s -o /dev/null -w "$e %{http_code}\n" -H "Authorization: Bearer $TOKEN" \
       https://homeassistant.kmkdp.com/api/states/$e; done     # 200 each, 404 = not loaded
   ```
   - both Workout pages: no "Entity not available" rows on Strength;
   - `https://home.kmkdp.com/workout/?date=<a day it is planned>` shows the item with a form sized to its kind;
   - `sudo docker logs homeassistant 2>&1 | grep -i 'invalid config' | tail` on kdk-dkr-02 is quiet;
   - wger app, both phones: the routine's day shows the change.

## CSV schema check (after a dashboard deploy that changes the log format)

The `/workout/` CSV is v2 since 2026-11 (columns in
[Dashboard → Workout log](../services/dashboard.md#workout-log-csv-v2-and-json)).
The app migrates a v1 file itself at startup and keeps a `.v1-<YYYYMMDD>.bak`
copy beside it.

**kdk-dkr-01, as kdkadmin**
```bash
D=/opt/kdk-lab/dashboard/workout-log
sudo head -1 "$D/workout-log.csv"     # timestamp,date,person,item,slug,set_index,weight,reps,seconds,lengths,minutes,score,done,source
sudo ls -l "$D"                       # workout-log.v1-<YYYYMMDD>.bak present after the first v2 start
sudo docker logs dashboard-workout 2>&1 | grep -i 'migrated' | tail -1
```

Week JSON through a logged-in browser session (there is no API token; the
route sits behind Authelia like the page). Easiest: open
`https://home.kmkdp.com/workout/api/week.json` (and `?person=rach`) in the
browser you are logged in with. From a shell, reuse that browser's
`authelia_session` cookie (DevTools → Application → Cookies → home.kmkdp.com);
it is read without echo and never printed:

**Laptop, any directory**
```bash
URL='https://home.kmkdp.com/workout/api/week.json?person=rach'
read -rs -p 'authelia_session cookie: ' COOKIE; echo
curl -s -b "authelia_session=$COOKIE" "$URL" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["week"], len(d["days"]), {k: v["count"] for k, v in d["goals"].items()})'
unset COOKIE                          # expect: 2026-Wnn 7 {'walks': .., 'pt': .., ...}
```

## Today page check on the phone (after a dashboard deploy that touches `/workout/`)

The Today page is also an installable web app with an offline queue
([Dashboard → Workout Today page](../services/dashboard.md#workout-today-page)).
Check with each person's phone:

1. Open `https://home.kmkdp.com/workout/` in Safari, sign in. Header shows the
   date, "Logging as <name>" and `0 / M done` (or the day's count).
2. Share → **Add to Home Screen** → open **Workout** from the home screen. The
   app has its own login: sign in once inside it. Open it once more while
   online so the service worker has the page.
3. Log one set on any card: the page comes back with a green
   `logged HH:MM · 1 set` rail.
4. **Airplane mode on.** Close and reopen the app: the cached page shows. Fill a
   card and tap **Log sets**: the card shows **queued, will sync** and the
   banner counts it.
5. **Airplane mode off**, bring the app to the foreground: the banner says
   "Synced 1 queued log", the page reloads and the rail is there.
6. On kdk-dkr-01 the rows are in the CSV:
   **kdk-dkr-01, as kdkadmin**
   ```bash
   sudo tail -n 5 /opt/kdk-lab/dashboard/workout-log/workout-log.csv   # one row per set, set_index 1..n, source web
   ```
7. Optional, login expiry: the replay of a queued log after the Authelia
   session ends shows **Reload to sign in**; after signing in the queue
   replays (nothing is lost).

If the app keeps showing an old page after a deploy, close it from the app
switcher and reopen it (the new service worker takes over on the next load).

## Rollback

Revert the PR and let Komodo redeploy `home-assistant`, then reload all YAML
(step 5), and re-run `gen_wger.py --apply` (step 6) so wger follows. Removed helpers come back with their last restored value. Re-run the
guide generator from the reverted vault state if the guide changed too.

## Gotchas

- Package filenames must be slugs (`workout_strength_rachel.yaml`, never a
  hyphen) — HA silently skips an invalid package name.
- HA YAML dashboard `url_path`s need a hyphen, so the pages are
  `/treadmill-workouts` (Jacob) and `/workout-rachel`; registering a **new**
  dashboard file is a host `configuration.yaml` edit + restart.
- The weight step is 0.5 lb; the finger-stretcher levels (6.6 / 8.8 / 11) are
  logged as typed — HA only checks min/max on `set_value`.
- The recorder keeps 10 days. Long-term history is the `/workout/` CSV
  (`/opt/kdk-lab/dashboard/workout-log/workout-log.csv` on kdk-dkr-01).

Related: [Home Assistant](../services/home-assistant.md) ·
[Dashboard](../services/dashboard.md) ·
[Treadmill bridge](treadmill-glassos-bridge.md)
