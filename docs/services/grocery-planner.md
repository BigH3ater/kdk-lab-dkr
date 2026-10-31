---
title: grocery-planner — weekly meal-plan + shopping-list builder
host: kdk-dkr-01
stack: grocery-planner
tier: apps
status: new
storage: no
secrets: [dashboard-tandoor-rw, vikunja-api-rw]
updated: 2026-10-31
tags: [homelab/stack, tier/apps, status/new, tandoor, grocery]
---

# grocery-planner — weekly meal-plan + shopping-list builder

A one-shot on [[kdk-dkr-01]] that turns the owner's meal rules into next week's
[[tandoor|Tandoor]] meal plan and a two-store shopping list, then raises one
combined [[dashboard|Vikunja]] approval task (paged to the phone by
[[home-assistant|Home Assistant]], the [approval contract](../runbooks/approval-tasks.md)).
It is **Tandoor-driven, human-submitted**: everything up to the store's front
door is automated; a person does the final order at New Pi (Coralville pickup)
and Target (Drive Up). There is no New Pi / Target ordering API, by design. The
plan page lives in the dashboard mealplan sidecar at `home.kmkdp.com/meal/groceries`.

**Related:** [[tandoor]] · [[dashboard]] · [[home-assistant]] · [grocery-planner runbook](../runbooks/grocery-planner.md) · [approval tasks](../runbooks/approval-tasks.md) · [recipe candidates](../reference/grocery-recipe-candidates.md)

## At a glance

| Field | Value |
|---|---|
| Runs | `grocery-weekly` Thu 18:03 (plan + list + task); `grocery-weekly-poll` Sat 07:05 (read the decision); `grocery-bulk` Thu 18:09 but the job acts only the first Thursday of an odd month (every 2 months, N=8). All: procedure → stack (deploy=false, destroy_before_deploy) on [[kdk-dkr-01]] |
| Image | `kdk/grocery-planner` built locally (`auto_pull=false`, `run_build=true`); python 3.12-slim + requests + pyyaml, uid 1000, read-only rootfs, no caps, 256 MB, one-shot |
| Output | stdout JSON lines → Loki (`job="grocery-planner"`); the page `home.kmkdp.com/meal/groceries`; a Vikunja approval task |
| Secrets | `dashboard-tandoor-rw` (the sidecar's Tandoor account), `vikunja-api-rw` (approval task) — op-injected into `.env` |
| Blast radius | writes Tandoor meal-plan + shopping-list entries and one Vikunja task; never orders or pays |

## How a week flows

1. **Thu 18:03** — `grocery-weekly` picks the season by date (Spring Mar–May,
   Summer Jun–Aug, Fall Sep–Nov, Winter Dec–Feb), generates 7 dinners
   (1 fish + 4 chicken breast + 1 ground beef + 1 steak), fish on Sunday,
   salmon/tuna and Greek/Asian beef alternating by ISO-week parity, a rotating
   veg side per dinner (no repeats), 2 lunch preps at 4 servings, and the
   weekly-staples + snack feeder recipes. It writes the meal-plan entries
   (idempotent per week key `gp:<ISO week>`), materializes the shopping list
   (2 servings; lunches 4; on-hand foods dropped), splits it by the store prefix
   of each food's supermarket category, and creates **one** combined Vikunja
   approval task with `APPROVALS-CHOICES: all / newpi / target`, due Fri 20:00,
   reminder Fri noon.
2. **Fri** — HA pages the phone; the owner approves both, one store, or lets it
   lapse (nothing is marked bought).
3. **Sat 07:05** — `grocery-weekly-poll` reads the decision into the Loki audit
   trail. The owner opens `home.kmkdp.com/meal/groceries`: New Pi as a tap-to-tick
   in-store checklist, Target with per-item `target.com` search links for the
   Drive Up cart, and a manual list with an add box. Ticks write `checked` back
   to Tandoor, so Tandoor stays the source of truth.

## Design decisions

- **Page route:** the page is added to the **dashboard mealplan sidecar** as
  `/groceries` (served at `home.kmkdp.com/meal/groceries`), not a new container.
  The sidecar already has the Tandoor allauth login, the CSRF/session handling,
  the Kodiak styling, and the Authelia ForwardAuth route; a separate `/groceries`
  router would duplicate all of it for no gain.
- **Poll strategy:** a **second scheduled pass** (`grocery-weekly-poll`), not a
  blocking wait. The approval is asynchronous and durable (the Vikunja task is
  the record), so the build pass exits immediately and the poll pass reads the
  outcome later. No long-lived container.
- **Bulk schedule:** the `grocery-bulk` procedure fires **every Thursday**, but
  the job gates to the **first Thursday of an odd month** (every two months,
  N = 8 weeks) in code — this keeps the cron unambiguous (no day-of-month /
  day-of-week OR surprise). Costco only; the Amana beef eighth-share reorder is
  Phase 2 (a low-stock task, stubbed).

## Recipes are real, imported, and scored

We do **not** write recipes. `recipes/candidates/*.yaml` holds real published
recipes per slot with the ratings read from each page's schema.org data. A
Bayesian score and a good/acceptable/rejected verdict come from `config.yaml`
(`grocery/scoring.py`); the review table is
[recipe candidates](../reference/grocery-recipe-candidates.md). The owner-run
seed imports the chosen recipe for each slot through Tandoor's URL import
(`recipe-from-source`), then tags it, adds it to the season book, and routes its
foods. See the [runbook](../runbooks/grocery-planner.md).

## What it does not do

No store ordering/cart API (New Pi runs its own storefront; Target dropped
Instacart for Shipt and has no public consumer order API). Browser automation of
either store is a ToS breach and is not built. The job stops at an approved,
aisle-sorted list; a person submits the order.
