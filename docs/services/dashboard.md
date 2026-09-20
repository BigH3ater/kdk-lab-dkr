---
title: Home dashboard
host: kdk-dkr-01
stack: dashboard
tier: internal
status: live
storage: yes
secrets: [vikunja-app, jellyfin-api-ro, seerr-api-ro, audiobookshelf-api-ro, vikunja-api-ro, dashboard-tandoor-rw]
updated: 2026-09-20
tags: [homelab/stack, tier/internal, status/live]
---

# Home dashboard

Family-facing launcher for the Mack household: service tiles + health, plain-language
how-to guides, a checkable task app, the weekly meal plan, and a household calendar.
**LAN / Tailscale only** (no Cloudflare tunnel); no Authelia (LAN is the gate) so the
whole family can use it without a directory identity.

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[tandoor-recipes]] · [[jellyfin]] · [[seerr]] · [[audiobookshelf]]

## At a glance

| Field | Value |
|---|---|
| Purpose | One home screen for watch/listen/read/request/cook/tasks/calendar |
| Status | 🟢 Live (P1); meal-plan sidecar = P2, calendar = P3 |
| Host | [[kdk-dkr-01]] |
| Endpoints | `home.kmkdp.com` (+ `/guides`, `/meal`), `tasks.kmkdp.com` |
| Images | `ghcr.io/gethomepage/homepage`, `nginx:1.29-alpine`, `vikunja/vikunja`, `kdk/dashboard-mealplan` (built) |
| Deploy | Komodo stack `dashboard` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/dashboard/vikunja` (SQLite + files) |
| Blast radius | Cosmetic — a launcher; the underlying services are unaffected |

## Verify

```bash
docker ps --filter name=homepage --filter name=vikunja --filter name=dashboard-guides --filter name=dashboard-mealplan --format '{{.Names}} {{.Status}}'
curl -s -o /dev/null -w '%{http_code}\n' https://home.kmkdp.com/            # 200
curl -s -o /dev/null -w '%{http_code}\n' https://home.kmkdp.com/guides/     # 200
curl -s -o /dev/null -w '%{http_code}\n' https://tasks.kmkdp.com/           # 200
curl -s https://home.kmkdp.com/meal/mealplan.json | head -c 200            # JSON (P2)
```

## Architecture

Homepage (`:3000`) serves `home.kmkdp.com` on the internal `proxy` net. `guides` (nginx `:80`)
and `mealplan` (`:8080`) are routed at `/guides` and `/meal` via Traefik `StripPrefix` with
higher router priority than the Homepage catch-all. Vikunja (`:3456`, own auth, SQLite) is at
`tasks.kmkdp.com`. All `*.kmkdp.com` service URLs the widgets/monitors point at resolve to the
DMZ (`192.168.191.20`) and are reachable (internal→DMZ:443 is open); Calibre is internal at
`10.1.20.20:8081`.

The meal-plan sidecar logs into Tandoor with a dedicated account (session auth — REST tokens
are disabled) and reads `/api/meal-plan/`; ratings post back as CookLog entries. Per-user
ratings remain available by opening the recipe page (each meal deep-links there).

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/dashboard/config/*.yaml` | Homepage services/bookmarks/widgets/settings (git-tracked) |
| `stacks/dashboard/guides/*.html` | Family how-to pages (relative links; served under `/guides`) |
| `stacks/dashboard/mealplan/app.py` | Tandoor session read + rating write-back |
| `stacks/dashboard/.env.tpl` | op refs (Vikunja JWT, widget API keys, Tandoor creds, ICS URL) |

## Secret Rotation

Widget API keys are read-only — rotate in each app, update the matching `*-api-ro` op item,
redeploy. The `dashboard-tandoor-rw` account password rotates in Tandoor + op. Vikunja JWT
rotation logs everyone out (harmless; they re-login).

## User Guide

The dashboard *is* the user guide: `home.kmkdp.com` → **How-To Guides** (`/guides/`) covers
recipes, watching, requests, audiobooks, books, photos, and budget in plain language.
