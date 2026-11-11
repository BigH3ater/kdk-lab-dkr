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

   **Laptop, repo root**

   ```sh
   cd stacks/grocery-planner && python3 -m grocery.seed
   ```

   It prints the supermarkets, namespaced categories, keywords, four season
   books, and the chosen recipe imports.

5. **Apply the seed** (writes to Tandoor — idempotent, safe to re-run):

   ```sh
   # on kdk-dkr-01, in the Komodo stack dir (holds the op-rendered .env and the built image)
   cd /opt/kdk-lab/stacks/grocery-weekly/stacks/grocery-planner
   sudo docker compose run --rm grocery grocery.seed            # dry run: prints the plan + preflight
   sudo docker compose run --rm grocery grocery.seed --apply    # writes to Tandoor
   # success ends with a summary of created recipes/books/categories; an import error
   # ("No module named grocery") means the image is older than the -I fix: redeploy grocery-weekly first
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

7. **First weekly run.** Either wait for the Tuesday 18:03 build pass, or trigger
   the `grocery-weekly` procedure in Komodo once. Confirm: meal-plan entries for
   the coming Sun–Sat, a shopping list split New Pi / Target, and a Vikunja
   approval task due Wednesday 20:00. Open `home.kmkdp.com/meal/groceries`.

## The weekly timeline (owner 2026-11)

New Pi now **delivers on Saturday** and needs 2–3 days' lead, so:

| Day | What |
|---|---|
| **Tuesday 18:03** | build pass: plan next week, build the New Pi + Target lists, raise the approval task (due Wed 20:00, reminder Wed noon) |
| **Wednesday night** | owner approves; the New Pi order must go in by now |
| **Thursday 07:05** | poll pass applies the decision (single store: the other store's entries for the week are deleted); the page shows the approved lists. Re-polls at 12:05 and 17:05 catch a late decision |
| **Friday night** | owner places the Target Drive Up order (no lead time) from the same list |
| **Saturday** | New Pi delivers |
| **Sunday** | prep day + the fresh-fish dinner (salmon keeps 1–2 days; tuna weeks have no timing constraint) |

The times are config values (`schedule`, `approvals.weekly`); the Komodo crons
in `komodo/resources.toml` mirror them, off the gitops `:00/:15/:30/:45` slots.

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
- **Dinner mix:** `proteins` (fish 1, poultry 2, ground_beef 2, steak 1; a roast
  week drops ground_beef to 1). `beef` sets the ground-beef variety rule
  (`no_repeat_within_week`, `max_asian_per_week`); feta rides on the weekend eggs.
- **Flex dinner:** the `flex` block — `enabled` and `rotation` (default
  `[pork, chicken]`; parity A → rotation[0], B → rotation[1]). The flex dinner
  takes one slot to stretch the Amana beef; a chicken flex is an extra chicken
  dish not already used that week. Set `flex.enabled: false` to drop it (that
  slot becomes a 3rd ground beef).
- **Pork chops** are fresh/weekly at New Pi; add candidates in
  `recipes/candidates/<season>.yaml` under `<season>-pork-*` slots.
- **Roast dinner:** the `roast` block — `enabled`, `parity` (`A` = odd ISO week =
  every other week), `night` (**Wednesday**, owner decision) and `leftovers`. On
  a roast week the roast replaces one **ground-beef** dinner; the 3.5 lb roast's
  leftovers are the Thursday lunch (the entry is created without `addshopping`).
  Mississippi pot roast is the default; the roast beef is Amana freezer stock
  (`food_onhand`), so only the ranch/au jus packets, pepperoncini and butter reach
  the Target list.
- **Breakfast protein:** ready-to-drink shakes in "Weekly staples" — Chobani
  (7/wk, Target, weekly) + Jocko (7/wk) + Nurri (7/wk). Nurri routes to Costco
  and is bought by hand (the Costco bulk run was dropped 2026-11-04; no list
  carries it). **Jocko's store is unconfirmed**; it
  defaults to Target/weekly — *owner TODO: say where Jocko is bought* (set its
  rule in `recipes/foods.yaml`; a `Costco/bulk` category keeps it off every list).
- **Pets:** dog/pet food is a JustFoodForDogs subscription, budgeted in Actual
  and **not ordered here** — the config `exclude` list keeps any such item off
  every store list.
- **Store routing:** `stores` and `recipes/foods.yaml` (per-food category; a
  `pack_size` is kept on pack items but no run rounds with it since the bulk
  run was dropped). Move a food's
  category once in Tandoor to override permanently.
- **Cadence:** weekly times in `schedule` + `approvals.weekly`.
- **Owner review gate:** `require_owner_approval` (default on). The seed imports,
  and the generator uses, ONLY candidates with `approved: true` (or
  `owner_pick: true`). A candidate's owner state lives in the candidate YAML:
  `approved`, `rejected_by_owner`, `pending_owner_approval` (shown to the owner
  but not used yet), `owner_pick` (an owner-supplied recipe that bypasses the
  rating/count gate — the hard rules still apply). The review note
  (`kodiak-codex/.../grocery-recipes.md`) is the source; `scripts`-free: a `[x]`
  sets `approved`, `[ ]` sets `rejected_by_owner`.
- **Excluded ingredients:** `exclude_ingredients` (radish, beet, arugula) — the
  scorer rejects any candidate whose title/ingredients contain one, in every
  season.
- **Short side pool:** `sides.max_repeats_per_week` (default 2). When fewer than
  7 sides are approved, the generator repeats a side up to this many times (more
  only if the pool is tiny) so the week still fills; the preflight reports the
  per-season side count vs 7. Approve more sides to drop the repeats.

## New Pi product links (tap-to-order on the groceries page)

The New Pi tab of `home.kmkdp.com/meal/groceries` can show each line as a link
straight to the item on New Pi's online market, so ordering is one tap per item.
A food gets its link from an optional `newpi_url` on its rule in
`recipes/foods.yaml`:

```yaml
- {match: spinach, category: "NewPi/weekly: Produce",
   newpi_url: "https://market.newpi.coop/product/39574", newpi_note: "organic baby spinach, 5 oz"}
```

- **One source of truth.** The dashboard mealplan sidecar mounts this same
  `foods.yaml` read-only (`../../stacks/grocery-planner/recipes/foods.yaml` →
  `/app/foods.yaml`, see `stacks/dashboard/compose.yaml`) and matches each New Pi
  line to a rule with the **same longest-match** the seed uses to route the food
  (`grocery.seed.route_food`). So the link always comes from the rule that put
  the food at New Pi, and there is no second copy to keep in sync. A line whose
  rule has no `newpi_url` shows a small `+` "no link yet" marker. `newpi_note` is
  only a human hint for picking the right product; it is not shown on the page.
- **Deploy.** Links are read at container start, so after editing `foods.yaml`
  **redeploy the `dashboard` stack** for the New Pi tab to pick them up (the
  weekly `grocery-*` run does **not** need re-running — links don't touch the
  Tandoor list, only how it's displayed).
- **No prices, ever.** Put only the product URL/note in the repo; this repo is
  public and prices/budget stay in the vault.

### Finding a product id

New Pi runs a Local Food Marketplace (LFM) single-page storefront. Product ids
are **global and share-safe**: `https://market.newpi.coop/product/<id>` resolves
to the item after a one-time store pick (New Pioneer Co-op locations are stores
`5369`, `5370`, `5371`). That `/product/<id>` form is what goes in `newpi_url`.

Names and prices render client-side (there is no documented, cookieless
name→id search URL), so there are two ways to get an id:

1. **By hand (reliable):** open `https://market.newpi.coop`, pick your co-op,
   search for the item, open the product, and copy the `/product/<id>` part of
   the URL. Prefer the staple the household actually buys (plain, sensible size,
   the local/organic default New Pi stocks).
2. **Helper (`stacks/grocery-planner/scripts/newpi_lookup.py`):**

   **Laptop, repo root**

   ```sh
   cd stacks/grocery-planner
   # Every product id in a store's category, from the public sitemap (always works):
   python3 scripts/newpi_lookup.py list --category produce --store 5369
   # Best-effort name search via the storefront API (prints id / name / size):
   python3 scripts/newpi_lookup.py search spinach "chicken breast"
   ```

   `list` is sitemap-based and reliable but id-only. `search` tries the LFM
   product-search endpoint; the exact path is undocumented, so if the site has
   changed it prints what it got and falls back to the by-hand method above. The
   tool uses a normal User-Agent, a low request rate, no login, and prints no
   prices.

Add a link **only when you are sure of the product** — a wrong link is worse
than none. `tests/test_newpi_links.py` guards the shape: a `newpi_url` must be a
real `market.newpi.coop/product/<id>` URL on a rule that routes to New Pi.

## Buy-unit conversion (one buyable amount per food)

The groceries page adds up every recipe line for a food. Recipes use cups,
tablespoons, cloves and counts, but the store sells heads, pounds and bags, so a
food's rule in `recipes/foods.yaml` can carry an optional `buy:` map that turns
the sum into ONE store amount:

```yaml
- {match: garlic, category: "NewPi/weekly: Produce", buy: {unit: head, clove: 10, tsp: 10}}
- {match: carrot, category: "NewPi/weekly: Produce", buy: {unit: lb, cup: 3.5, each: 8, oz: 16}}
```

- **`unit`** (required) is what the store sells. Every other key is **how much
  of that recipe unit makes ONE `unit`**: `clove: 10` = 10 cloves a head,
  `cup: 3.5` = 3.5 cups chopped a pound. Keys: `tsp`/`tbsp`/`cup`, `oz`/`lb`/`g`/`kg`,
  `each` (bare counts like "2 carrots"), or any other unit name the recipes use
  (`clove`, `bag`, `block`, `slice`). Give one key per family; tsp, Tbsp and cup
  convert into each other, and so do oz, lb and g. The buy unit itself always
  counts 1:1.
- **What the page shows.** The buy amount first, then the raw recipe sum muted in
  brackets: `garlic  2 heads (1 Tbsp + 8¾ clove)`, `carrots  1¾ lb (1¾ cup + 1 lb + ¼)`.
  Weight units (lb, oz, kg, g) round **up** to the next ¼; everything else rounds
  up to the next whole (`2 heads`, `2 bags`); `unit: each` shows a bare number.
  A recipe unit with no key in `buy` is not guessed: it stays listed after the
  buy amount (`1 head + 2 slice`). A food with no `buy:` shows exactly as before.
- **Same rule lookup as the links.** The sidecar picks the rule with the same
  longest match as `newpi_url` (`stacks/dashboard/mealplan/newpi.py`), so
  `garlic powder` (its own rule, no `buy`) is never converted to heads.
- **Deploy.** Like links, `buy:` is read at container start: redeploy the
  `dashboard` stack after editing `foods.yaml`. The weekly run does not need
  re-running.
- **No prices.** `buy:` holds amounts only.

Check a change before merging (the tests cover the owner's garlic and carrot
lines and guard the `buy:` shape):

**Laptop, repo root**

```sh
REPO=$(git rev-parse --show-toplevel)
python3 -m pip install pytest==8.4.2 flask==3.0.3 requests==2.32.3 waitress==3.0.1 pyyaml==6.0.3
cd "$REPO/stacks/dashboard/mealplan" && python3 -I -m pytest -q tests
cd "$REPO/stacks/grocery-planner" && python3 -m pytest -q tests/test_newpi_links.py
```

After the `dashboard` redeploy, confirm the sidecar sees the new rules (read-only):

**kdk-dkr-01 as kdkadmin, from the laptop**

```sh
HOST=10.1.20.20
FOOD=garlic
ssh kdkadmin@$HOST "sudo -n docker exec dashboard-mealplan python -c 'import newpi; print(newpi.buy_for(\"$FOOD\"))'"
# expect a dict such as {'unit': 'head', 'head': 1, 'clove': 10, 'vol': 30}; None means
# the container still has the old foods.yaml (redeploy) or the rule has no buy map
```

## Pantry tab (reorder an on-hand food)

The planner keeps the `pantry:` foods in `recipes/cleanup.yaml` and every
stock-store food (Amana beef, O'Connell chicken + pork, Penzeys spices) on hand,
so they never reach a weekly list. The **Pantry** tab on
`home.kmkdp.com/meal/groceries` is where a running-low jar goes back on the list.

- **What it lists.** First **Used this week**: the pantry/stock foods this plan
  week's (Sun–Sat) recipes call for, each with the recipe names muted under it.
  Then the whole catalog, grouped by store and aisle exactly as the weekly run
  routes the food (`foods.yaml` longest match). No second list: edit
  `cleanup.yaml` / `foods.yaml` and redeploy the `dashboard` stack.
- **reorder** adds ONE Tandoor shopping entry for the food: amount 1, unit = the
  rule's `buy:` unit if it has one (else none), no recipe link. The food's on-hand
  flag is **not** changed, so the next weekly build is unaffected. Target foods
  then show on the Target tab and in the Wednesday approval; stock-store foods
  (Penzeys, Amana, O'Connell) show on the Manual tab. Tick it off like any line.
- **on the list** replaces the button while an open (unchecked) recipe-less entry
  for the food exists, so it cannot be added twice; ticking that entry brings the
  button back.
- **The poll keeps it.** A single-store approval only deletes entries linked to
  this week's meal plan; a reorder has no recipe link, so it survives
  (`tests/test_poll_pass.py::test_recipe_less_reorder_survives_a_single_store_approval`).

Check the sidecar sees the pantry list after a `dashboard` redeploy (read-only):

**kdk-dkr-01 as kdkadmin, from the laptop**

```sh
HOST=10.1.20.20
FOOD=cumin
ssh kdkadmin@$HOST "sudo -n docker exec dashboard-mealplan python -c 'import stock; print(len(stock.pantry_names()), stock.route_for(\"$FOOD\"))'"
# expect: 52 Penzeys/restock: Spices  (0 = cleanup.yaml not mounted: check the compose volumes)
```

See the reorder entries that are open now (read-only, logs only show names):

**kdk-dkr-01 as kdkadmin, from the laptop**

```sh
HOST=10.1.20.20
SINCE=24h
ssh kdkadmin@$HOST "sudo -n docker logs --since $SINCE dashboard-mealplan 2>&1 | grep 'grocery reorder'"
```

## Thaw tab (freezer to fridge)

Beef (Amana), chicken breast and pork chops (O'Connell) and the Costco frozen
salmon are freezer stock: on hand, never on a list. The **Thaw** tab lists them
from the meal plan so they come out of the freezer in time.

- **Rows.** For each planned meal from today through a week out, every ingredient
  that routes to a freezer aisle of a stock or bulk store (Amana "Freezer Beef",
  O'Connell "Freezer", Costco "Freezer"; Penzeys is stock but not frozen) gives
  one row: meal date · recipe · food · amount (buy unit, else the recipe amount
  scaled to the plan's servings) and **move to the fridge by** a day and time.
  Rows are sorted by that time; a late one shows red.
- **Lead times** live in `config.yaml` → `thaw:` (`default: 24h`,
  `ground beef: 24h`, `chops: 24h`, `chicken breast: 24h`, `roast: 48h`,
  `salmon: 12h`, `tuna: 12h`). The longest key that is part of the food name wins; values are
  hours (`24h`) or days (`2d`). The time counts back from the meal (breakfast
  08:00, lunch 12:00, dinner 18:00): chili on Wednesday → ground beef by Tue 6 PM.
- **Tick** a row when it is in the fridge (tap again to undo). Ticks are local to
  the sidecar (`/data/thaw.json` on the `dashboard_mealplan-data` volume), not
  Tandoor; ticks for meals before the current plan week are dropped, so the tab
  starts clean each Sunday.
- **Meals tab** opens with one line, "Thaw today: 1 lb ground beef (Wed Beef
  Chili)", for open rows due today or overdue.
- **Reserved from the freezer** (under the rows): what the Tuesday build took
  off the freezer counts for this plan week and the next (cut, amount, day,
  recipe), from `freezer.json` `reservations`.
- **Change a lead time**: edit `thaw:` in `stacks/grocery-planner/config.yaml`,
  merge, redeploy the `dashboard` stack (the file is read at container start).

Check the lead table and the tick file (read-only):

**kdk-dkr-01 as kdkadmin, from the laptop**

```sh
HOST=10.1.20.20
FOOD="chuck roast"
ssh kdkadmin@$HOST "sudo -n docker exec dashboard-mealplan python -c 'import stock; print(stock.thaw_hours(\"$FOOD\"))'"
# expect 48.0 for a roast; 24.0 everywhere = the planner config is not mounted
ssh kdkadmin@$HOST "sudo -n docker exec dashboard-mealplan cat /data/thaw.json"
# {"done": {...}} once something is ticked; "No such file" before the first tick
```

Run the page tests before merging:

**Laptop, repo root**

```sh
REPO=$(git rev-parse --show-toplevel)
python3 -m pip install pytest==8.4.2 flask==3.0.3 requests==2.32.3 waitress==3.0.1 pyyaml==6.0.3
cd "$REPO/stacks/dashboard/mealplan" && python3 -I -m pytest -q tests/test_pantry_tab.py tests/test_thaw_tab.py
cd "$REPO/stacks/grocery-planner" && python3 -m pytest -q tests/test_poll_pass.py tests/test_shoplist.py
```

## Freezer inventory (Pantry tab, Freezer section)

What is in the chest freezer, so the plan never picks a dinner the freezer can't
cover. One file on kdk-dkr-01: `/opt/kdk-lab/dashboard/pantry/freezer.json`
(mounted at `/pantry` in `dashboard-mealplan` and in the grocery-planner job;
schema in `stacks/grocery-planner/grocery/freezer.py`, version 1). Units per cut:
**portions** (steaks, patties, fish), **lb** (ground beef, roasts, stew meat,
fajita strips, brats), **packages** (O'Connell chicken, pork chops, bacon until
you set portions per package).

- **Correct a count.** Pantry tab → Freezer: `−` / `+` (one portion, package or
  lb) or type a number and **set**. A **fill in** row (count unknown: tuna,
  salmon in the seed) is ignored by the planner until you set it. A **set
  portions** row (O'Connell packs) takes portions per package; until then a
  recipe's chicken counts by the 1.1 lb package. **add** adds a cut (name, unit,
  count, package size); map it in `config.yaml` → `freezer.map` (food-name
  substring → cut id) so recipes use it.
- **Order arrived: O'Connell / Amana** adds that standing order
  (`config.yaml` → `standing_orders`) to the counts. **undo last change** swaps
  `freezer.json` with `freezer.json.bak` (every write keeps one `.bak`; press
  again to redo). Each change logs `freezer change by <user>: …` in the sidecar.
- **What the build does** (Tuesday): skips a dinner/lunch the freezer is short
  for (if nothing in the slot fits it plans one anyway and logs
  `freezer_short_pick`); fish parity yields to the fish in stock
  (`freezer_fish`); prefers the cut with the most dinners' worth left; after
  writing the plan it decrements what it reserved (`freezer_reserved`, also on
  the Thaw tab) — once per week. Freezer-aisle foods with no `freezer.map` key
  log `freezer_unmapped` and count as available. Missing or unreadable file:
  `freezer_inventory_missing`, plan as before.

**Install the seed (once, after the PR merges and the dashboard redeploys).**
Copies `stacks/grocery-planner/freezer-seed.json` (the 2026-11-11 Amana +
O'Connell paperwork) only if no inventory exists yet; never overwrites one.

**kdk-dkr-01 as kdkadmin, from the laptop**

```sh
HOST=10.1.20.20
DIR=/opt/kdk-lab/dashboard/pantry
SEED=/opt/kdk-lab/stacks/dashboard/stacks/grocery-planner/freezer-seed.json
ssh kdkadmin@$HOST "sudo -n install -d -o 1000 -g 1000 -m 755 $DIR && { sudo -n test -e $DIR/freezer.json || sudo -n install -o 1000 -g 1000 -m 644 $SEED $DIR/freezer.json; } && sudo -n ls -l $DIR"
# expect freezer.json, 1000 1000, -rw-r--r--
```

Then correct the counts on the Pantry tab (the seed is delivered counts; use
since delivery is unknown) and fill in tuna and salmon.

Check what the last build reserved (read-only):

**kdk-dkr-01 as kdkadmin, from the laptop**

```sh
HOST=10.1.20.20
ssh kdkadmin@$HOST "sudo -n docker logs grocery-planner 2>&1 | grep -E 'freezer_(reserved|short|fish|unmapped|inventory_missing)'"
```

## Preview a week before its run

The Tuesday build plans the NEXT Sun–Sat week. To see what it would plan, and the
store lists that would follow, without writing anything:

**On kdk-dkr-01 as kdkadmin**

```bash
cd /opt/kdk-lab/stacks/grocery-weekly/stacks/grocery-planner
sudo docker compose run --rm grocery grocery.run --preview                       # the next run's week
sudo docker compose run --rm grocery grocery.run --preview --week-of 2026-11-15  # any week (a Sunday)
```

It prints the meals per day, then each store's lines by aisle. Look for repeats,
lines under `Other` (a missing rule in `recipes/foods.yaml`), and anything that
should be on hand. If the week is already planned it says so; the real run skips it.

## Stock stores: Amana beef, O'Connell chicken + pork, Penzeys spices

Chicken breast and boneless pork chops come from O'Connell Organic Acres (frozen;
farm van on the Iowa City route, 3rd Monday monthly, or FedEx) and beef from the
Amana eighth share. Both stores are `stores.freezer_stock_stores`: their foods are
marked on hand and never appear on a weekly list. Size an order from the menu:

**On kdk-dkr-01 as kdkadmin**

```bash
cd /opt/kdk-lab/stacks/grocery-weekly/stacks/grocery-planner
sudo docker compose run --rm grocery grocery.run --stock                   # next 4 weeks (--freezer works too)
sudo docker compose run --rm grocery grocery.run --stock --weeks 24        # one beef share
```

It prints pounds per food and, where `foods.yaml` gives `pack_lb`, packs to order.

## Clean up ingredients (one food per line)

Recipe-site imports leave one food under many names ("garlic", "minced garlic",
"garlic cloves") and some lines unread ("▢ 1 pound carrots"). `grocery.cleanup`
repoints the season-book and fixed recipes' ingredients to one canonical food and
unit, marks pantry staples on hand, and adds Tandoor automations so new imports
arrive clean. Rules live in `recipes/cleanup.yaml`; store routing in `recipes/foods.yaml`.
Run it from `/opt/kdk-lab/stacks/grocery-weekly/stacks/grocery-planner`:

**On kdk-dkr-01 as kdkadmin**

```bash
cd /opt/kdk-lab/stacks/grocery-weekly/stacks/grocery-planner
sudo docker compose run --rm grocery grocery.cleanup                 # dry run: every change
sudo docker compose run --rm grocery grocery.cleanup --test          # per-recipe shopping test, read-only
sudo docker compose run --rm grocery grocery.cleanup --apply --refresh-list
sudo docker compose run --rm grocery grocery.cleanup --test-live     # adds each recipe to the list, checks, deletes
```

`--test` exits non-zero and prints `FIX` lines for a recipe whose shopping lines
break a rule: an unread name, an unknown unit, dark meat, an excluded ingredient,
pet food, or a food with no store rule. Fix the YAML and re-run. Re-run the
clean-up after importing new recipes. Tandoor's food merge is not used (it can't
be undone); old spellings stay as unused foods. To put a pantry food back on the
list, untick "on hand" on the food in Tandoor.

**On hand is only for pantry and stock foods.** Tandoor leaves an on-hand food
off every meal-plan list, and on hand never expires. Only the `pantry` list in
`cleanup.yaml` and the stock stores (Amana, O'Connell, Penzeys) should be on
hand. Any other on-hand food is a **stray**: `--test` prints it as a `FIX` line,
the Tuesday build logs `event=stray_onhand` (with the recipes that use it),
`--preview` lists it under "NOT LISTED", and `--apply` unticks it so it comes back
on the list. Week 46 lost the chili's kidney beans and the squash's fresh
rosemary this way. On hand was set in Tandoor, not by this code: ticking an item
in Tandoor's own shopping list marks its food on hand when the user's
**Settings → Shopping → "Add on hand"** (`shopping_add_onhand`) is on, so keep it
off for jmack and rach (the groceries page tick does not do this).

The groceries page has a tab per list (New Pi, Target, Manual, Meals; the tab
is in the URL, e.g. `/meal/groceries#target`). It shows one line per food: same-family units are added up
(tsp/Tbsp/cup, oz/lb/g), with the recipes underneath; ticking the line ticks all
its entries.

## Household, space & meal-plan sharing (owner)

Tandoor scopes data at two levels, both confirmed read-only (dashboard account,
2026-11-01):

- **Space** — the household space is **"Mack House" (id 3)**; the `dashboard`
  account is a member of exactly that one space, so every write (recipes, books,
  meal plans, shopping entries, food categories) lands there. The job verifies
  the active space at login and fails loudly if it isn't `tandoor.space` /
  `space_id`.
- **Household** — within a space, Tandoor 2.x shares meal plans and shopping
  lists by **Household** (`UserSpace.household`; the space has
  `allow_sharing: true`). This is why the old per-user `plan_share` /
  `shopping_share` preferences are gone — they're **absent from this instance's
  `/api/user-preference/` and openapi `UserPreference` schema** — and why the
  meal-plan/shopping serializers carry no `shared` field: sharing is implicit
  via the household. The `dashboard` account is in household **"The Macks"
  (id 1)**. The job verifies the active household at login too
  (`tandoor.household` / `household_id`) and fails loudly otherwise.

**Owner step — household membership.** For jmack and rach to see the meal plan
and the shared shopping list automatically, they must be in the **same
household** ("The Macks") as the `dashboard` account. Adding a user to a
household is a **space-admin** action (`/api/household/` is admin-only and
returns 403 to the `dashboard` account, so the job can't do it and can't even
enumerate the other users' households): in Tandoor, **Space settings →
Households**, put jmack (id 3) and rach (id 4) in "The Macks". Once they share a
household, no per-plan sharing is needed. The weekly run logs a `sharing_note`.

## Amana ground-beef runway

An Amana eighth is ~30 lb ground beef + patties, plus 8 roasts (~3.5 lb each)
and the steaks. With the current mix, ground-beef **dinners average ~1.5/week**
(2 most weeks, 1 on the every-other-week roast weeks = (2+1)/2), each ~1 lb for
two, plus a beef lunch prep in alternate weeks (~0.5 lb/wk). So beef runs
~1.5 + 0.5 ≈ **2 lb/week** → 30 lb lasts **~15 weeks**, and with the patties
**~15–18 weeks** — lining up with the **~16 weeks** the 8 roasts last (one every
other week) and the steaks (~1/week). If the beef runs out first, the owner's
options are Amana's add-on 1-lb ground-beef packages or reordering the share
sooner (a low-stock reorder task is Phase 2).

**Phase 2 idea (not built):** once a freezer inventory exists, the flex dinner
becomes "ground beef when stock is high, pork or chicken when it's low".

After a rules change, run the tests: `cd stacks/grocery-planner && uv run pytest` (uv builds the env from `uv.lock`; add a dependency with `uv add <pkg>`, never by hand in the Dockerfile).

## Refresh ratings later

**Laptop, repo root**

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
- **A recipe ingredient never reached the list.** Check the food in Tandoor: on
  hand (see "On hand is only for pantry and stock foods" above), ignore shopping
  (a `not_food` name such as "diced" means the line was mis-read: run the clean-up
  `--apply`, it re-reads it), or a Costco/stock store (not a weekly list).
- **Week already planned.** A re-run logs `skip: week already planned` — the week
  key marker (`gp:<ISO week>`) is already on the meal-plan entries. Delete those
  entries in Tandoor to force a regenerate. A config change never reaches a
  planned week: when the planned week has fewer dinners or lunch preps than the
  config now asks for, the skip line is `level: warn` with `short` (W46 was
  built 2026-11-01 with 2 lunch preps, hours before the 3-batch rule merged, and
  stayed at 2). Add the missing entries by hand or delete and regenerate.
- **A dinner has no veg side.** Its main already holds a vegetable from
  `sides.vegetables` in `config.yaml` (whole-word match on the Tandoor food
  name; optional lines ignored). Edit that list to change what counts.
- **Rice side missing or unwanted.** Tag the recipe `serve-with-rice` in Tandoor
  to force the "Brown Rice (microwave packet)" side, or `no-rice-side` to stop a
  wrong detection. A recipe that lists its own raw rice (e.g. "white rice")
  keeps that line and gets no packet. If the build logs `setup` it created the
  rice recipe / Snack meal type that run; nothing to do.
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

- **A scheduled run left no trace.** Query Loki
  `{job="grocery-planner"}` (container `grocery-planner`, `-poll`);
  every run pushes its JSON lines there itself, plus a `run_error` line if it
  crashed. No lines at all means the container never started: check the
  Komodo update for the stack (pre_deploy / Compose Up). On kdk-dkr-01 the
  stack's `.env` must list the `GROCERY_*` keys (from the stack `environment`)
  **and** the four op keys (key names only:
  `sudo cut -d= -f1 /opt/kdk-lab/stacks/<stack>/stacks/grocery-planner/.env`).
  A `.env` with only the op keys means pre_deploy overwrote it again
  (poll runs as a plain weekly pass). `loki push failed` on stderr means Loki was down; the stdout copy is
  still in `docker logs grocery-planner<suffix>`.

## Rollback

Freezer inventory only: delete the `/opt/kdk-lab/dashboard/pantry` volume line
from `stacks/grocery-planner/compose.yaml` (the build then logs
`freezer_inventory_missing` and plans as before) and, to drop the Pantry-tab
section, the same line from `stacks/dashboard/compose.yaml`. The host file can
stay.

Whole service:

1. Remove the three `grocery-*` stacks and procedures from
   `komodo/resources.toml` in a PR; merge; let Komodo pick up the removal. The
   one-shots leave no running container.
2. Delete `stacks/grocery-planner/` and the grocery routes block in
   `stacks/dashboard/mealplan/app.py` (the `# ---- Groceries page` section) in
   the same or a follow-up PR; redeploy the dashboard stack.
3. Tandoor data (the books, categories, keywords, imported recipes, meal-plan and
   shopping-list entries) is harmless to leave; delete in the Tandoor UI if you
   want a clean slate. No host state, no secrets beyond the two reused op items.
