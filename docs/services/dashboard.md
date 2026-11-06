---
title: Home dashboard
host: kdk-dkr-01
stack: dashboard
tier: internal
status: live
storage: yes
secrets: [vikunja-app, jellyfin-api-ro, seerr-api-ro, audiobookshelf-api-ro, vikunja-api-ro, dashboard-tandoor-rw]
updated: 2026-11-06
tags: [homelab/stack, tier/internal, status/live]
---

# Home dashboard

Family-facing launcher for the Mack household: service tiles + health, plain-language
how-to guides, a checkable task app, the weekly meal plan, and a household calendar.
**LAN / Tailscale only** (no Cloudflare tunnel); **Authelia ForwardAuth** on all
home.kmkdp.com routers (two_factor, group `dashboard-users` = jmack + rach) —
the Remote-User header drives rating/task attribution.

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[tandoor]] · [[jellyfin]] · [[seerr]] · [[audiobookshelf]]

## At a glance

| Field | Value |
|---|---|
| Purpose | One home screen for watch/listen/read/request/cook/tasks/calendar |
| Status | 🟢 Live (P1); meal-plan sidecar = P2, calendar = P3 |
| Host | [[kdk-dkr-01]] |
| Endpoints | `home.kmkdp.com` (+ `/guides`, `/meal`, `/workout`), `tasks.kmkdp.com` |
| Images | `ghcr.io/gethomepage/homepage:v2.7.0`, `nginx:1.31.8-alpine`, `vikunja/vikunja:2.7.1`, `kdk/dashboard-mealplan` (built) |
| Deploy | Komodo stack `dashboard` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/dashboard/vikunja` (SQLite + files) |
| Blast radius | Cosmetic — a launcher; the underlying services are unaffected |

## Verify

```bash
ssh kdkadmin@10.1.20.20 "sudo docker ps --filter name=homepage --filter name=vikunja --filter name=dashboard-guides --filter name=dashboard-mealplan --filter name=dashboard-workout --format '{{.Names}} {{.Status}}'"
curl -s -o /dev/null -w '%{http_code}\n' https://home.kmkdp.com/            # 200
curl -s -o /dev/null -w '%{http_code}\n' https://home.kmkdp.com/guides/     # 200
curl -s -o /dev/null -w '%{http_code}\n' https://tasks.kmkdp.com/           # 200
curl -s https://home.kmkdp.com/meal/mealplan.json | head -c 200            # JSON (P2)
ssh kdkadmin@10.1.20.20 'sudo docker exec dashboard-workout python -c "import urllib.request;print(urllib.request.urlopen(\"http://127.0.0.1:8080/healthz\").read())"'  # b'ok'
```

## Architecture

Homepage (`:3000`) serves `home.kmkdp.com` on the internal `proxy` net. `guides` (nginx `:80`)
and `mealplan` (`:8080`) are routed at `/guides` and `/meal` via Traefik `StripPrefix` with
higher router priority than the Homepage catch-all. Vikunja (`:3456`, Authelia OIDC, SQLite) is at
`tasks.kmkdp.com`. All `*.kmkdp.com` service URLs the widgets/monitors point at resolve to the
DMZ (`192.168.191.20`) and are reachable (internal→DMZ:443 is open); Calibre is internal at
`10.1.20.20:8081`.

The meal-plan sidecar logs into Tandoor with a dedicated account (session auth — REST tokens
are disabled) and reads `/api/meal-plan/`; ratings post back as CookLog entries. Per-user
ratings remain available by opening the recipe page (each meal deep-links there).

An **Approvals** project (with labels `approved`, `skipped`, `notified`) drives the generic
approval-task loop: tasks needing a decision raise an actionable phone notification via
[[home-assistant|Home Assistant]], which polls this project and records the decision back on the
task. Home Assistant holds a narrow `vikunja-api-approvals` token; Vikunja stays the source of
truth. See the [approval-tasks runbook](../runbooks/approval-tasks.md).

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/dashboard/config/*.yaml` | Homepage services/bookmarks/widgets/settings (git-tracked) |
| `stacks/dashboard/guides/*.html` | Family how-to pages (served under `/guides`; each sets `<base href="/guides/">` so relative links survive the StripPrefix) |
| `stacks/dashboard/guides/shared-nav.{css,js}` | The one Kodiak sidebar shared by Homepage, guides and `/meal` (same links/order/labels, active-link state, mobile top-bar overlay); `shared-nav.js` hides it inside an iframe so the embedded `/meal` panels stay bare |
| `stacks/dashboard/mealplan/app.py` | Tandoor session read + rating write-back |
| `stacks/dashboard/workout/app.py` | `/workout/` **Today page** (see [Workout Today page](#workout-today-page)): the day's steps from the host-managed `workout.json` with poster, dose, cues and a per-set log form per exercise; writes `/opt/kdk-lab/dashboard/workout-log/workout-log.csv` (CSV v2, see [Workout log](#workout-log-csv-v2-and-json)) and the person's HA helpers (`Remote-User` jmack → Jacob, rach → Rachel, anyone else read-only) with the `home-assistant-workout-token`; serves `/api/history.json` + `/api/week.json` and the installable-app files (manifest, service worker, icons). `history.py` = schema, migration and the read side; `migrate_csv.py` = the same migration by hand. Reads the catalog from the repo clone (`stacks/home-assistant/tools`). See the [workout-tracking-update runbook](../runbooks/workout-tracking-update.md) |
| `stacks/dashboard/guides/gen-workout-guide.py` | Builds the host-managed Workout guide (`/guides/workout/`) + `workout.json` (v2: per-step `cues` from the vault card generator `ex_days.py`, `sets` from the dose) from the private vault on the laptop; prefers `ex-<slug>-rom.png` pictures. Exercise changes: [workout-tracking-update runbook](../runbooks/workout-tracking-update.md) |
| `stacks/dashboard/.env.tpl` | op refs (Vikunja JWT, widget API keys, Tandoor creds, ICS URL) |

## Workout Today page

`https://home.kmkdp.com/workout/` — one page per day, built for the phone.

- **Which day.** `?date=YYYY-MM-DD` (default today, America/Chicago); the week
  strip links each day plus the previous/next week. `?day=mon` still works (that
  weekday of the current week). A past date is **backdating**: rows get that
  `date`, Home Assistant is left alone (it keeps today's values). A future date
  shows the plan without buttons; the server refuses a future `date`.
- **Header:** date, day name, summary, "You need", "Logging as …" and
  `N / M done` (M = the planned items: the day's steps mapped through the
  catalog, the Every-day block and the daily check-ins, same list as
  `api/week.json`).
- **Cards** in vault order: poster, dose, cues (from `workout.json` v2), and a
  form sized to the kind: `w`/`wt`/`wl`/`r`/`t` = one row per set (count =
  `sets` in `workout.json`, else the number before "×" in the dose, else 3),
  `min` = one minutes box, `done` = one tick (a routine such as the hand
  routine or the flexibility flow gets its tick on its **first** step only),
  `score` items sit in the **Daily check-in** card. Steps without a catalog
  item (warm-ups) show poster, dose and cues only.
- **Prefill** = the last CSV session before this date (row *n* = set *n*, extra
  rows repeat the last set); with no CSV history, the person's HA values. Clear
  a row you did not do: every filled row is logged. "Copy set 1 down" (JS only)
  copies row 1 into the others.
- **Logging.** `POST /workout/log` writes one CSV row per filled set
  (`set_index` continues after sets already logged that day, so "Log more sets"
  adds 4, 5 …); HA gets the **last** filled set and the done time (today only).
  A card with rows for that person/date gets a green rail
  `logged HH:MM · n sets` and its form folds into "Log more sets" (a tick
  disappears once done). The footer says **Day complete ✓** or lists what is
  still to do; links to History (placeholder until the history views) and
  the Guide.

**Installable app + offline queue.** The page links a web manifest (`Workout`,
standalone, icons drawn by the app itself) and registers a service worker
(`/workout/sw.js`, scope `/workout/`):

| Piece | Behaviour |
|---|---|
| Service worker | Pages: network first, the cached copy when offline (keyed by URL without `ok`/`err`; `/workout/` offline falls back to the cached `?date=<today>` or the last `/workout/`). Shell (manifest, icons, shared nav) and posters: cache first, refreshed in the background. Caches **only** same-origin `200`s that were not redirected, so an Authelia login redirect never lands in the cache. `POST /log` and `api/*` are never touched. Cache name `workout-<SW_VERSION>`; bump `SW_VERSION` in `app.py` when the page shell changes. |
| Offline queue | With JS the form is sent with `fetch` (header `X-Workout-Queue: 1`, JSON reply). A network failure or 5xx puts the submission in `localStorage` (`workout-queue-v1`) and the card shows **queued, will sync** (amber rail). Replay on page load, on the `online` event, when the app comes back to the foreground, and every 30 s while anything is queued; then the page reloads to show the rails. |
| Login expired | A replay or submit answered by a redirect (302/303 to Authelia) or a 401 keeps the queue, deletes the `workout-*` caches and shows **Reload to sign in**; after the login the queue replays. A 4xx answer (bad numbers) drops that entry and says so. |
| Duplicates | Each submission carries a random `qid`; the server remembers the last 500 per process, so a replay of a POST that did land (answer lost) is not written twice. |
| JS off | Forms post normally and the server answers with a 303 back to the same date. No queue, no service worker. |

Limits: the queue lives on that phone/browser only (Safari and the home-screen
app on iOS have **separate** storage and logins: sign in inside the app once);
a queued row's `timestamp` is the replay time (its `date` is the page's date);
the `qid` memory is lost on a container restart; an offline page is the copy
from the last time that date was opened online (a banner says so when the
day has changed since).

## Workout log (CSV v2 and JSON)

The CSV is the durable record (HA's recorder keeps 10 days). It lives in
`/opt/kdk-lab/dashboard/workout-log/` on kdk-dkr-01 (the container's only
writable path, `/log`) and is in the nightly + offsite backup with the rest of
`/opt/kdk-lab`. One row per logged set or tick:

| Column | Meaning |
|---|---|
| `timestamp` | ISO time with offset (America/Chicago) |
| `date` | the local day the row counts for (`YYYY-MM-DD`) |
| `person` | catalog person key (`jacob`, `rachel`) |
| `item` | catalog item id (`box_squat`, `treadmill_walk` …) |
| `slug` | vault picture slug the form sat next to (blank for check-ins) |
| `set_index` | `1`, `2` … for a set; blank for a tick, a score or a migrated row |
| `weight` `reps` `seconds` `lengths` `minutes` `score` | the values; blank when not logged (`lengths` = 10-ft lengths, kind `wl`) |
| `done` | `1` for a ticked routine |
| `source` | `web` (the page), `treadmill` (auto-log, later), `migrated` (v1 rows) |

**Migration.** v1 (`timestamp,person,item,slug,weight,reps,seconds,minutes,score,done`)
is upgraded when the app starts and again before its first write: the old file is
copied to `workout-log.v1-<YYYYMMDD>.bak` beside it, then rewritten atomically
(temp file in `/log` + `os.replace`) with `date` from the timestamp,
`set_index`/`lengths` blank and `source=migrated`. A v2 file is left alone; an
unknown header is refused (the app will not start; nothing is changed). To
preview or run it by hand:

**kdk-dkr-01, as kdkadmin**
```bash
C=dashboard-workout
sudo docker exec "$C" python /app/migrate_csv.py --dry-run   # prints the plan, changes nothing
sudo docker exec "$C" python /app/migrate_csv.py             # migrate (no-op when already v2)
```

**JSON routes** (behind Authelia like the page; for the catalog people only,
either person's data, default = the viewer; `person` = login `jmack`/`rach` or
key `jacob`/`rachel`):

| Route | Returns |
|---|---|
| `GET /workout/api/history.json?item=<id>[&person=][&since=YYYY-MM-DD]` | `sessions`: one per day, `sets` (weight/reps/seconds/lengths/minutes, set index, source), `top` set, `done`, `score`; plus `treadmill_minutes` (daily totals by `source`) for `treadmill_walk` |
| `GET /workout/api/week.json[?person=][&week=2026-W45]` | the ISO week (Mon–Sun, default this week): per day `planned` (from `workout.json` + catalog), `done`, `missing`, `day_complete`, `walk_minutes`, `strength_session`; `goals` with count/target/met for walks (7 days and ≥125 min), PT (3), hands (3), stretches (7), flexibility (3), strength (4: Mon/Tue/Thu/Fri with every lift), pain (7: knee + hand) |

Unknown viewer → 403; bad `item`/`person`/`since`/`week` → 400.

## Secret Rotation

Widget API keys are read-only — rotate in each app, update the matching `*-api-ro` op item,
redeploy. The `dashboard-tandoor-rw` account password rotates in Tandoor + op. Vikunja JWT
rotation logs everyone out (harmless; they re-login).

## User Guide

The dashboard *is* the user guide: `home.kmkdp.com` → **How-To Guides** (`/guides/`) covers
recipes, watching, requests, audiobooks, books, photos, and budget in plain language.
