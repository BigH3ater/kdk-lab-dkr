---
title: Home dashboard
host: kdk-dkr-01
stack: dashboard
tier: internal
status: live
storage: yes
secrets: [vikunja-app, jellyfin-api-ro, seerr-api-ro, audiobookshelf-api-ro, vikunja-api-ro, dashboard-tandoor-rw]
updated: 2026-11-09
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
| Endpoints | `home.kmkdp.com` (+ `/guides`, `/meal`), `tasks.kmkdp.com` |
| Images | `ghcr.io/gethomepage/homepage:v2.7.0`, `nginx:1.31.8-alpine`, `vikunja/vikunja:2.7.1`, `kdk/dashboard-mealplan` (built) |
| Deploy | Komodo stack `dashboard` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/dashboard/vikunja` (SQLite + files) |
| Blast radius | Cosmetic — a launcher; the underlying services are unaffected |

## Verify

```bash
ssh kdkadmin@10.1.20.20 "sudo docker ps --filter name=homepage --filter name=vikunja --filter name=dashboard-guides --filter name=dashboard-mealplan --format '{{.Names}} {{.Status}}'"
curl -s -o /dev/null -w '%{http_code}\n' https://home.kmkdp.com/            # 200
curl -s -o /dev/null -w '%{http_code}\n' https://home.kmkdp.com/guides/     # 200
curl -s -o /dev/null -w '%{http_code}\n' https://tasks.kmkdp.com/           # 200
curl -s https://home.kmkdp.com/meal/mealplan.json | head -c 200            # JSON (P2)
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

The **Tasks** strip (`/meal/tasks`, a 175px Homepage iframe) reads every Vikunja project with the
jmack token, following `x-pagination-total-pages` (Vikunja caps a page at 50 rows), and shows
**every open task**, one column per project. Each column lists the overdue, undated and
due-within-14-days tasks (it scrolls inside the card) under a header with the count and the
number late; tasks due later sit in a collapsed `+N later` fold. Nothing is cut: before
2026-11-09 each column stopped at 6 rows and later tasks were dropped, so 28 of 83 open chores
showed. Ticking a box completes the task with the viewer's own token when one is stored, else
jmack's.

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
| `stacks/dashboard/mealplan/app.py` | Tandoor session read + rating write-back; groceries snapshot cache (env `GROCERY_TTL_S`, `GROCERY_REFRESH_S`, `RECIPE_TTL_S`); the meal page repeats the planner's Sunday prep lunches as "Lunch prep" on Mon–Sat of that plan week (Tandoor filters meal plans on `to_date` only, so a Sun→Sat entry would drop out of some windows) |
| `stacks/dashboard/.env.tpl` | op refs (Vikunja JWT, widget API keys, Tandoor creds, ICS URL) |

## Performance: groceries page

`/meal/groceries` renders from an in-memory snapshot of the Tandoor shopping list, the
two-week meal plan and the planned recipes. A warmer thread re-reads it every
`GROCERY_REFRESH_S` (120 s): 2 calls when recipes are cached (one `page_size=200` shopping
page plus the meal plan), plus one parallel `/api/recipe/<id>/` per planned recipe every
`RECIPE_TTL_S` (30 min). A snapshot older than `GROCERY_TTL_S` (120 s) is still served while
a background refresh runs. After 15 min, or on a new day, the request refreshes before it
renders. Ticks mark the cached entry checked at once, and add/reorder re-read the list
before the redirect, so the page never shows a ticked item again. **updated … · refresh**
(`?refresh=1`) forces a re-read for changes made in Tandoor itself.

| 2026-11-08, timed inside the container | Before (measured) | After (render measured on live data, cache path from tests; re-time after deploy) |
|---|---|---|
| Page view, recipes cached | ~1.0 s, 3 Tandoor calls | ~0.12 s render, 0 calls |
| Page view after 10 min idle | ~4.9 s, 19 calls (16 recipes in a row + photo re-reads) | ~0.12 s, 0 calls (the refresh runs in the background, ~1.1 s, 18 parallel calls) |

Each render logs `groceries render ms=… source=cache|stale|sync age_s=…` and each refresh
logs `grocery refresh reason=… ms=… upstream_calls=… upstream_ms=…`. Query them in Loki with
`{host="kdk-dkr-01",container="dashboard-mealplan"} |= "groceries render"`.

## Retired: Workout guide and /workout/ Today page

Both were retired on 2026-11-06 when the owner chose [[wger]] in the exercise-tracking
pilot: `home.kmkdp.com/workout/` (the `dashboard-workout` sidecar, its CSV log and HA
token) and `/guides/workout/` (the host-managed guide in `/opt/kdk-lab/dashboard/workout`)
no longer exist; exercise logging and the plan with pictures are in the wger app. The
guide generator survives as a laptop-only build step for wger
(`stacks/home-assistant/tools/gen-workout-guide.py`, output to a scratch dir, never
published). Since 2026-11-07 wger has a **Workouts** tile in the Home group and a
**Workouts** sidebar link; the sidebar's two HA workout links became one **Treadmill**
link (`/treadmill-workouts`), removed 2026-11-08 with the treadmill HA integration. Removal and host clean-up: [workout-tracking-update runbook](../runbooks/workout-tracking-update.md#retirement-of-the-in-house-tracker-2026-11-06).

Since 2026-11 the Home group also has a **Search** tile for [[searxng]]
(`search.kmkdp.com`); its siteMonitor polls `/healthz`, which Authelia bypasses.

## Secret Rotation

Widget API keys are read-only — rotate in each app, update the matching `*-api-ro` op item,
redeploy. The `dashboard-tandoor-rw` account password rotates in Tandoor + op. Vikunja JWT
rotation logs everyone out (harmless; they re-login).

## User Guide

The dashboard *is* the user guide: `home.kmkdp.com` → **How-To Guides** (`/guides/`) covers
recipes, watching, requests, audiobooks, books, photos, and budget in plain language.
