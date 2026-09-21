# dashboard

Mack family home dashboard on **kdk-dkr-01**, **LAN / Tailscale only** (no Cloudflare
tunnel). Auth = Authelia ForwardAuth (two_factor, group `dashboard-users`); tiles open
services that carry their own login (most now Authelia OIDC, incl. Home Assistant).

| Service | URL | What it is |
|---|---|---|
| `homepage` | `https://home.kmkdp.com` | The dashboard (tiles, health, calendar, meal plan) |
| `guides` (nginx) | `https://home.kmkdp.com/guides/` | Plain-language family how-to pages |
| `vikunja` | `https://tasks.kmkdp.com` | Checkable household tasks (own auth, SQLite) |
| `mealplan` (P2) | `https://home.kmkdp.com/meal/` | Tandoor weekly plan + star-rating write-back |

## Config (all git-tracked)
- `config/` — Homepage YAML (`settings`, `services`, `bookmarks`, `widgets`, `custom.css`).
- `guides/` — static HTML guides (relative links; served under `/guides` via Traefik StripPrefix).
- `mealplan/` — the sidecar (`Dockerfile` + `app.py`).
- `.env.tpl` — op refs, injected at deploy (`pre_deploy`).

## Secrets (create in 1Password **before** the first deploy)
`op inject` fails if any ref is missing. Vault **kdk-cluster** is writable; **kdk-ops** is read-only.
- `op://kdk-cluster/vikunja-app/jwt_secret` — random 64+ chars (Vikunja session secret).
- `op://kdk-cluster/jellyfin-api-ro/password`, `.../seerr-api-ro/password`,
  `.../audiobookshelf-api-ro/password`, `.../vikunja-api-ro/password` — read-only API keys
  minted in each app for the Homepage stat widgets (field `password`).
- `op://kdk-cluster/dashboard-tandoor-rw/{username,password}` — dedicated Tandoor account
  for the meal-plan sidecar (**P2**); must be **added to the "Mack House" space** in Tandoor.
- `op://kdk-ops/proton-calendar-ics/url` — reused from planner-service (calendar widget, **P3**).

If a key isn't ready, comment out its line in `.env.tpl` **and** its widget block in
`config/services.yaml` to bring the rest up; `siteMonitor` up/down health needs no key.

## Notes
- `guides` and `mealplan` are routed at subpaths of `home.kmkdp.com` (StripPrefix `/guides`,
  `/meal`) with higher router priority than the Homepage catch-all.
- Tandoor REST tokens are disabled, so the sidecar uses **session login**; ratings post as
  CookLog entries under the shared account with the rater's name in the comment (per-user
  ratings still available by opening the recipe page). See `mealplan/app.py` header.
- Deploy is standard GitOps: register in `komodo/resources.toml`, push, run `gitops-sync`.
