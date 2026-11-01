---
title: Grocery planner — seed, run, and tune
---

# Grocery planner

How the weekly grocery run works, how to seed Tandoor the first time, add or
swap a recipe, change the rules, and troubleshoot. Service reference:
[grocery-planner](../services/grocery-planner.md). Approval mechanism:
[approval tasks](approval-tasks.md).

Everything the planner does is driven by `stacks/grocery-planner/config.yaml`
(rules, caps, scoring, store routing, cadence) and the recipe candidate files —
change those, not the code.

## One-time setup (owner hand steps)

Do these in order, from the laptop (`op` + SSH agent). The seed is **not** run by
the build agent; you run it after reviewing the candidates.

1. **Create the Approvals project token wiring** if not already done for the
   [approval mechanism](approval-tasks.md). Note the Approvals **project id**.

2. **Review the recipe candidates.** Open
   [recipe candidates](../reference/grocery-recipe-candidates.md) (or regenerate
   it: `python3 -m grocery.seed --report docs/reference/grocery-recipe-candidates.md`
   from `stacks/grocery-planner/`). For each slot, the default pick is the
   top-scoring **good** (then **acceptable**) candidate. To choose a different
   one, set `selected: true` on that candidate in `recipes/candidates/<season>.yaml`.
   Slots with no good/acceptable candidate are listed; add a candidate URL by
   hand (any public recipe page Tandoor can import) or raise a cap in
   `config.yaml` (see below) and re-run the report.

3. **Set the Approvals project id** in `stacks/grocery-planner/.env.tpl`
   (`VIKUNJA_APPROVALS_PROJECT=<id>`), then merge the PR so Komodo renders `.env`.

4. **Dry-run the seed** to see exactly what it will create (no writes):

   ```sh
   cd stacks/grocery-planner && python3 -m grocery.seed
   ```

   It prints the supermarkets, namespaced categories, keywords, four season
   books, and the chosen recipe imports.

5. **Apply the seed** (writes to Tandoor — idempotent, safe to re-run):

   ```sh
   docker compose -f stacks/grocery-planner/compose.yaml run --rm grocery grocery.seed --apply
   ```

   This creates the stores + `<store>/<cadence>: <aisle>` categories, the keyword
   vocabulary, the Spring/Summer/Fall/Winter books, imports each chosen recipe
   via Tandoor's URL import, sets `servings = 2`, tags it, adds it to its season
   book, and routes its foods (`recipes/foods.yaml`). **Verify the first import**
   in Tandoor (the scraped recipe shape varies by site); fix any that came in
   thin and re-run — already-imported URLs are skipped.

6. **Spot-check routing.** In Tandoor, open a few foods and confirm their
   supermarket category (`NewPi/weekly: Produce`, `Target/weekly: …`). Foods that
   matched nothing default to `Target/weekly: Other` and are logged — move them
   to the right category once; the assignment sticks.

7. **First weekly run.** Either wait for Thursday 18:03, or trigger the
   `grocery-weekly` procedure in Komodo once. Confirm: meal-plan entries for the
   coming Sun–Sat, a shopping list split New Pi / Target, and a Vikunja approval
   task. Open `home.kmkdp.com/meal/groceries`.

## Add or swap a recipe

- **Swap the pick for a slot:** set `selected: true` on another candidate in
  `recipes/candidates/<season>.yaml`, re-run `--report`, merge, and re-run the
  seed `--apply` (it imports the newly selected URL; it does not delete the old
  recipe — remove that in Tandoor if you want).
- **Add a new candidate:** append an entry under the slot's `candidates:` with
  `site`, `url`, `rating`, `count`, `total_time_min`, `ingredients`, and the
  observation flags. Run `--refresh` to re-read its rating from the page, then
  `--report`.
- **New slot / dish:** the slots mirror §5c(4) of the design note. Add the slot
  id to the candidate file and (if it is a main/side/lunch) make sure the
  generator keywords in `grocery/seed.py::keywords_for_slot` cover it.

## Change the rules

All in `config.yaml`:

- **Caps:** `working_time_max` (active-time household rule the generator enforces
  on Tandoor's `working_time`), `candidate_total_time_max` / `lunch_total_time_max`
  (hard caps on a candidate's *published* total time, which includes passive
  marinade/bake time), `ingredient_max` (non-pantry), and `pantry_staples`.
- **Scoring:** `prior_m`, `prior_weight_C`, the `good` / `acceptable` thresholds,
  and `site_count_weight` per site. Lower `good.min_count` if the reachable sites
  give few 100+-review recipes.
- **Proteins / parity / feta / sides / lunches / snacks / seasons:** the
  `proteins`, `sides`, `lunches`, `snacks`, `seasons` blocks.
- **Roast dinner:** the `roast` block — `enabled`, `parity` (which week is a
  roast week; `A` = odd ISO week = every other week), `night` (**Wednesday**,
  owner decision 2026-10-31) and `leftovers`. On a roast week a slow-cooker roast
  replaces one chicken dinner (3 chicken + roast), and the 3.5 lb roast's
  leftovers are planned as Thursday lunch with no extra beef ordered (the
  Thursday entry is created without `addshopping`). The Mississippi pot roast is
  the default pick; the roast beef is Amana freezer stock (`food_onhand`), so only
  the ranch/au jus packets, pepperoncini and butter reach the Target list. Set
  `roast.enabled: false` to drop it (every dinner becomes 4 chicken again).
- **Store routing:** `stores` (aisle order, weekly/bulk stores, freshness
  defaults) and `recipes/foods.yaml` (per-food category). Move a food's category
  once in Tandoor to override permanently.
- **Cadence:** `cadence.bulk_weeks` (N), `bulk_first_week_only`,
  `bulk_month_parity`.

After a rules change, run the tests: `python3 -m pytest stacks/grocery-planner/tests/`.

## Refresh ratings later

```sh
cd stacks/grocery-planner && python3 -m grocery.seed --refresh --report docs/reference/grocery-recipe-candidates.md
```

`--refresh` re-reads each candidate URL's schema.org `aggregateRating` into the
YAML (one GET per page, best-effort; blocked sites are left unchanged), then the
report is regenerated. Commit the updated YAML + report.

## Fill a slot the scored set can't (bookmarklet import)

The automated scorer only sees the **reachable** recipe corpus — Allrecipes
blocks bots on purpose (HTTP 402) and is out of scope, and a few dishes (a
true Greek ground-beef-and-rice-with-feta skillet, salmon specifically with
carrots) have no well-rated page there. The seed **preflight** lists any season
short of the generator minimums and blocks `--apply` (use `--force` only to seed
what exists). The dry-run also lists slots with no good/acceptable candidate.

To add a recipe the scorer couldn't find, from a site that blocks our fetcher:

1. In Tandoor (recipes.kmkdp.com), Settings → enable the **Import bookmarklet**
   and drag it to your browser bar (one-time).
2. Open the recipe page in the browser (your own session, not a bot) and click
   the bookmarklet — Tandoor imports it server-side, which is not bot-blocked.
3. Add keywords by hand to match the slot: the season (`spring`/…), and
   `main`+`simple`+`mediterranean`+ the protein tag (`poultry`, `salmon`,
   `tuna`, `ground-beef`+`greek`/`asian`, `steak`) for a dinner, or `veg-side` /
   `lunch-prep`. Set `servings = 2` and add it to the season recipe book.
4. Record it in `recipes/candidates/<season>.yaml` (site, url, rating, count as
   seen) with `selected: true`, so the repo stays the source of truth and a
   later `--refresh` keeps its rating current.

**Current slots needing this** (non-blocking — slack beyond the weekly minimums,
so the generator still builds every week):

- `summer-lunch-4` — no rated chicken-quinoa meal-prep bowl under the caps.

**Longer term:** the household's own ratings become the score source. The
dashboard meal-plan sidecar already writes star ratings back as Tandoor CookLog
entries; a future `refresh` can prefer the household CookLog average over the
public rating, and demote dishes the household rates low (Phase 2).

## Troubleshooting

- **"plan_error" in the log / no task created.** The selected pool could not fill
  a protein slot for that season (e.g. no eligible salmon recipe under the caps).
  Add/select a candidate for the missing slot and re-seed, or raise the cap.
- **Week already planned.** A re-run logs `skip: week already planned` — the week
  key marker (`gp:<ISO week>`) is already on the meal-plan entries. Delete those
  entries in Tandoor to force a regenerate.
- **Nothing routed to a store / lots of "unrouted".** Foods lack a
  `supermarket_category`; run the seed (it routes recipe foods) or assign in
  Tandoor. The page groups unrouted items nowhere — fix the category.
- **Import came in thin.** Some sites expose little schema.org data; the
  `recipe-from-source` parse is only as good as the page. Pick a different
  candidate for that slot (different site) and re-seed.
- **Approval never pages / wrong buttons.** See the
  [approval tasks runbook](approval-tasks.md) troubleshooting — the grocery job
  only creates the task; HA owns the notification.
- **Page won't tick / add.** The sidecar writes with the Tandoor session + CSRF;
  check the dashboard mealplan container log (`dashboard-mealplan`) for a
  re-login loop.

## Rollback

1. Remove the three `grocery-*` stacks and procedures from
   `komodo/resources.toml` in a PR; merge; let Komodo pick up the removal. The
   one-shots leave no running container.
2. Delete `stacks/grocery-planner/` and the grocery routes block in
   `stacks/dashboard/mealplan/app.py` (the `# ---- Groceries page` section) in
   the same or a follow-up PR; redeploy the dashboard stack.
3. Tandoor data (the books, categories, keywords, imported recipes, meal-plan and
   shopping-list entries) is harmless to leave; delete in the Tandoor UI if you
   want a clean slate. No host state, no secrets beyond the two reused op items.
