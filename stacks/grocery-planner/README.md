# grocery-planner

Weekly meal-plan + shopping-list builder for the Mack House, on kdk-dkr-01.
Tandoor-driven, human-submitted (no store ordering API). Full design:
`docs/services/grocery-planner.md`; how-to: `docs/runbooks/grocery-planner.md`.

## What it does

- **Seed (owner, once):** `grocery/seed.py` creates the stores, namespaced
  supermarket categories (`<store>/<cadence>: <aisle>`), the keyword vocabulary,
  the four seasonal recipe books, and imports the **chosen real published
  recipe** for each slot via Tandoor's URL import, then tags + routes it. The
  recipes are not written by us; `recipes/candidates/*.yaml` holds rated
  candidates scored by `grocery/scoring.py` (Bayesian), reviewed in
  `docs/reference/grocery-recipe-candidates.md`.
- **Weekly job:** `grocery/run.py` generates next week's plan (season by date,
  1 fish + 4 chicken breast + 1 ground beef + 1 steak, fish Sunday, salmon/tuna
  + Greek/Asian beef by ISO-week parity, rotating veg sides, no repeats, caps),
  writes the meal-plan entries (idempotent per week key), builds the New Pi +
  Target list minus on-hand items, and raises one combined Vikunja approval task.
- **Page:** the dashboard mealplan sidecar serves `home.kmkdp.com/meal/groceries`
  (New Pi in-store checklist, Target list with search links, a manual add box);
  ticks write `checked` back to Tandoor.

## Layout

```
config.yaml              rules + scoring + store routing + caps (edit this, not code)
recipes/candidates/*.yaml real rated recipe candidates per season
recipes/foods.yaml       food -> store/aisle routing for the seed
grocery/                 scoring, generator, shoplist (pure); tandoor, vikunja, seed, run
tests/                   pytest, no network
```

## Run

```sh
# tests (no network)
cd stacks/grocery-planner && uv run pytest

# seed dry-run (prints the plan; writes nothing)
python3 -m grocery.seed
# owner seed (writes to Tandoor) -- after reviewing the candidates
docker compose -f stacks/grocery-planner/compose.yaml run --rm grocery grocery.seed --apply

# weekly build / poll / bulk are driven by the Komodo procedures (GROCERY_MODE/GROCERY_POLL)
```

Image built locally (`kdk/grocery-planner`); `komodo/resources.toml` sets
`auto_pull = false` + `run_build = true`. Baseline: the one service meets
`docs/reference/compose-baseline.md` (one-shot `healthcheck` skip).
