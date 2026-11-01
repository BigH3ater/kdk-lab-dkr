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
- `config/` — Homepage YAML (`settings`, `services`, `bookmarks`, `widgets`, `custom.css`, `custom.js`). `custom.css` `@import`s `/guides/shared-nav.css`; `custom.js` is a tiny loader that pulls in `/guides/shared-nav.js` (Homepage only auto-loads its own `custom.js`).
- `guides/` — static HTML guides. Served under `/guides` via Traefik StripPrefix; each page sets `<base href="/guides/">` so relative links/assets resolve under the prefix even without a trailing slash.
- `guides/shared-nav.css` + `guides/shared-nav.js` — the **one** Kodiak sidebar, shared by Homepage, the guides and the `/meal` pages (same link set/order/labels, active-link state, and a <960px top-bar + hamburger overlay). `shared-nav.js` injects the markup, marks the current surface with `aria-current="page"`, and **hides the nav inside an iframe** (so the dashboard-embedded `/meal` grid/tasks/weather stay bare; standalone `/meal/groceries` gets the sidebar). Served same-origin from `/guides`, behind Authelia — no CDN.
- `guides.nginx.conf` — mounted read-only over the container's `default.conf`; sends `Cache-Control: no-cache` for HTML and a short `max-age` + `must-revalidate` for css/js so guide edits show up without a hard refresh. Single-file bind mount → conf edits need a container recreate to apply.
- `mealplan/` — the sidecar (`Dockerfile` + `app.py`).
- `.env.tpl` — op refs, injected at deploy (`pre_deploy`).

## Secrets (create in 1Password **before** the first deploy)
`op inject` fails if any ref is missing. Stack-read items live in **kdk-cluster** (the only vault templates reference); **kdk-ops** is for management logins containers never read.
- `op://kdk-cluster/vikunja-app/jwt_secret` — random 64+ chars (Vikunja session secret).
- `op://kdk-cluster/jellyfin-api-ro/password`, `.../seerr-api-ro/password`,
  `.../audiobookshelf-api-ro/password`, `.../vikunja-api-ro/credential` — read-only API keys
  minted in each app for the Homepage stat widgets (field `password`).
- `op://kdk-cluster/dashboard-tandoor-rw/{username,password}` — dedicated Tandoor account
  for the meal-plan sidecar (**P2**); must be **added to the "Mack House" space** in Tandoor.
- `op://kdk-cluster/proton-calendar-ics/url` — household calendar widget (**P3**).

If a key isn't ready, comment out its line in `.env.tpl` **and** its widget block in
`config/services.yaml` to bring the rest up; `siteMonitor` up/down health needs no key.

## Notes
- `guides` and `mealplan` are routed at subpaths of `home.kmkdp.com` (StripPrefix `/guides`,
  `/meal`) with higher router priority than the Homepage catch-all.
- Tandoor REST tokens are disabled, so the sidecar uses **session login**; ratings post as
  CookLog entries under the shared account with the rater's name in the comment (per-user
  ratings still available by opening the recipe page). See `mealplan/app.py` header.
- Deploy is standard GitOps: register in `komodo/resources.toml`, push, run `gitops-sync`.
# Cache busting

The HTML references `style.css?v=<ver>`, `shared-nav.css?v=<ver>` and `shared-nav.js?v=<ver>` (and `custom.css`/`custom.js` and the `/meal` heads reference the same `shared-nav.*?v=<ver>`); bump the value (any new string) everywhere whenever a shared file changes so browsers that still hold an old copy fetch the new one on their next page load. The nginx headers (`guides.nginx.conf`) keep HTML at no-cache and css/js at five minutes, but a copy cached before 2026-10-30 had no headers at all and lived on heuristics, which is why the first sidebar fix did not show without a hard refresh.
