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
door is automated; a person does the final order at New Pi (Saturday delivery)
and Target (Drive Up). There is no New Pi / Target ordering API, by design. The
plan page lives in the dashboard mealplan sidecar at `home.kmkdp.com/meal/groceries`.

**Related:** [[tandoor]] · [[dashboard]] · [[home-assistant]] · [grocery-planner runbook](../runbooks/grocery-planner.md) · [approval tasks](../runbooks/approval-tasks.md) · [recipe candidates](../reference/grocery-recipe-candidates.md)

## At a glance

| Field | Value |
|---|---|
| Runs | `grocery-weekly` Tue 18:03 (plan + list + task); `grocery-weekly-poll` Thu 07:05 (read the decision); `grocery-bulk` Tue 18:09 but the job acts only the first Tuesday of an odd month (every 2 months, N=8). All: procedure → stack (deploy=false, destroy_before_deploy) on [[kdk-dkr-01]] |
| Image | `kdk/grocery-planner` built locally (`auto_pull=false`, `run_build=true`); python 3.12-slim + requests + pyyaml, uid 1000, read-only rootfs, no caps, 256 MB, one-shot |
| Output | stdout JSON lines → Loki (`job="grocery-planner"`); the page `home.kmkdp.com/meal/groceries`; a Vikunja approval task |
| Secrets | `dashboard-tandoor-rw` (the sidecar's Tandoor account), `vikunja-api-rw` (approval task) — op-injected into `.env` |
| Blast radius | writes Tandoor meal-plan + shopping-list entries and one Vikunja task; never orders or pays |

## How a week flows

1. **Tuesday 18:03** — `grocery-weekly` picks the season by date (Spring Mar–May,
   Summer Jun–Aug, Fall Sep–Nov, Winter Dec–Feb) and generates the 7 dinners for
   the plan week (Sun→Sat) after the Saturday delivery: Sunday fish + **2 chicken
   breast + 2 ground beef + 1 flex + 1 steak** (fish variant salmon/tuna by
   ISO-week parity). The **flex** dinner alternates pork chop / chicken breast by
   week; ground-beef dishes vary (no repeat, ≤1 Asian-style); feta rides on the
   weekend eggs. **Every other week (parity A) a Wednesday slow-cooker roast
   replaces one ground-beef dinner** (`roast_night`, owner decision), with its
   own veg side and its leftovers planned as **Thursday lunch** (no extra beef).
   It also plans a rotating veg side per dinner (no repeats), 2 lunch preps at 4
   servings, and the weekly-staples + snack feeders; writes the meal-plan entries
   (idempotent per week key `gp:<ISO week>`); materializes the shopping list via
   the native meal-plan add-to-shopping (2 servings; lunches 4; on-hand + pet
   foods dropped); splits it by the store prefix of each food's supermarket
   category; and creates **one** combined Vikunja approval task
   (`APPROVALS-CHOICES: all / newpi / target`), **due Wednesday 20:00, reminder
   Wednesday noon** — New Pi needs 2–3 days' lead for the Saturday delivery, so
   the order must go in by Wednesday night.
2. **Wednesday** — HA pages the phone; the owner approves both, one store, or lets
   it lapse (nothing is marked bought). Target Drive Up has no lead time and is
   still ordered Friday night for the Saturday delivery, from the same list.
3. **Thursday 07:05** — `grocery-weekly-poll` reads the decision into the Loki
   audit trail and hides the non-approved store. The owner opens
   `home.kmkdp.com/meal/groceries`: New Pi as a tap-to-tick checklist, Target with
   per-item `target.com` search links for the Drive Up cart, and a manual list
   with an add box. Ticks write `checked` back to Tandoor.
4. **Saturday** — New Pi delivers; Sunday is prep + the fresh-fish dinner (salmon
   keeps 1–2 days; tuna weeks have no timing constraint).

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
- **Bulk schedule:** the `grocery-bulk` procedure fires **every Tuesday**, but
  the job gates to the **first Tuesday of an odd month** (every two months,
  N = 8 weeks) in code — this keeps the cron unambiguous (no day-of-month /
  day-of-week OR surprise). Costco only; the Amana beef eighth-share reorder is
  Phase 2 (a low-stock task, stubbed).

## Recipes are real, imported, and scored

We do **not** write recipes. `recipes/candidates/*.yaml` holds real published
recipes per slot with the ratings read from each page's schema.org data. A
Bayesian score and a good/acceptable/rejected verdict come from `config.yaml`
(`grocery/scoring.py`); thresholds are calibrated for the **reachable** corpus
(Allrecipes is out of scope — it blocks bots with HTTP 402). The review table is
[recipe candidates](../reference/grocery-recipe-candidates.md). **The owner
approves picks** (`require_owner_approval`, on by default): the seed imports and
the generator use only `approved`/`owner_pick` candidates; `rejected_by_owner`
and `pending_owner_approval` are skipped. `exclude_ingredients` (radish, beet,
arugula) are rejected everywhere. The owner-run seed runs a **preflight** that
refuses to apply if any season is short of the hard weekly minimums (1 salmon +
1 tuna + 3 chicken + 2 ground beef + 1 pork + 1 steak + 1 roast + 2 lunches + ≥1
side; sides below 7 are reported, and the interim repeat rule covers them), then
imports each chosen recipe through Tandoor's URL import (`recipe-from-source`),
tags it, adds it to the season book, and routes its foods. Every write lands in
the **Mack House** space — the job verifies the active space at login and fails
loudly otherwise. Meal-plan per-entry sharing is an owner step (no `shared` field
in this API). The fixed breakfast/snack feeders are seeded directly (not URL
imports), and the weekly run fails loudly if any is missing. See the
[runbook](../runbooks/grocery-planner.md).

## What it does not do

No store ordering/cart API (New Pi runs its own storefront; Target dropped
Instacart for Shipt and has no public consumer order API). Browser automation of
either store is a ToS breach and is not built. The job stops at an approved,
aisle-sorted list; a person submits the order.
