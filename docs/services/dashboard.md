---
title: Home dashboard
host: kdk-dkr-01
stack: dashboard
tier: internal
status: live
storage: yes
secrets: [vikunja-app, jellyfin-api-ro, seerr-api-ro, audiobookshelf-api-ro, vikunja-api-ro, dashboard-tandoor-rw]
updated: 2026-10-30
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
| `stacks/dashboard/workout/app.py` | `/workout/` log page: the day's steps from the host-managed `workout.json` with a form per exercise; writes the person's HA helpers (`Remote-User` jmack → Jacob, rach → Rachel, anyone else read-only) with the `home-assistant-workout-token` and appends `/opt/kdk-lab/dashboard/workout-log/workout-log.csv` (`timestamp,person,item,slug,weight,reps,seconds,minutes,score,done`). Reads the catalog from the repo clone (`stacks/home-assistant/tools`). See the [workout-tracking-update runbook](../runbooks/workout-tracking-update.md) |
| `stacks/dashboard/guides/gen-workout-guide.py` | Builds the host-managed Workout guide (`/guides/workout/`) + `workout.json` from the private vault on the laptop; prefers `ex-<slug>-rom.png` pictures. Exercise changes: [workout-tracking-update runbook](../runbooks/workout-tracking-update.md) |
| `stacks/dashboard/.env.tpl` | op refs (Vikunja JWT, widget API keys, Tandoor creds, ICS URL) |

## Secret Rotation

Widget API keys are read-only — rotate in each app, update the matching `*-api-ro` op item,
redeploy. The `dashboard-tandoor-rw` account password rotates in Tandoor + op. Vikunja JWT
rotation logs everyone out (harmless; they re-login).

## User Guide

The dashboard *is* the user guide: `home.kmkdp.com` → **How-To Guides** (`/guides/`) covers
recipes, watching, requests, audiobooks, books, photos, and budget in plain language.
