---
title: Home dashboard
host: kdk-dkr-01
stack: dashboard
tier: internal
status: live
storage: yes
secrets: [vikunja-app, jellyfin-api-ro, seerr-api-ro, audiobookshelf-api-ro, vikunja-api-ro, vikunja-api-rw, dashboard-tandoor-rw, homepage-ha-token, proton-calendar-ics, dashboard-kitchen-device-key]
updated: 2026-11-10
tags: [homelab/stack, tier/internal, status/live]
---

# Home dashboard

Family-facing launcher for the Mack household: service tiles + health, plain-language
how-to guides, a checkable task app, the weekly meal plan, and a household calendar.
**LAN / Tailscale only** (no Cloudflare tunnel); **Authelia ForwardAuth** on all
home.kmkdp.com routers (two_factor, group `dashboard-users` = jmack + rach) —
the Remote-User header drives rating/task attribution. The kitchen screen uses
`kitchen.kmkdp.com` instead: the same kitchen views with no Authelia, gated by a
paired-device cookie (see [Kitchen host and device pairing](#kitchen-host-and-device-pairing-kitchenkmkdpcom)).

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[tandoor]] · [[jellyfin]] · [[seerr]] · [[audiobookshelf]]

## At a glance

| Field | Value |
|---|---|
| Purpose | One home screen for watch/listen/read/request/cook/tasks/calendar |
| Status | 🟢 Live (P1); meal-plan sidecar = P2, calendar = P3 |
| Host | [[kdk-dkr-01]] |
| Endpoints | `home.kmkdp.com` (+ `/guides`, `/meal`, kitchen board `/meal/kitchen`, pantry check `/meal/kitchen/pantry`, household page `/meal/home`, device admin `/meal/kitchen/devices`), `kitchen.kmkdp.com` (paired kitchen screen, no Authelia), `admin.kmkdp.com` (lab admin page, jmack only), `tasks.kmkdp.com` |
| Images | `ghcr.io/gethomepage/homepage:v2.7.0`, `nginx:1.31.8-alpine`, `vikunja/vikunja:2.7.1`, `kdk/dashboard-mealplan` (built) |
| Deploy | Komodo stack `dashboard` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/dashboard/vikunja` (SQLite + files), `/opt/kdk-lab/dashboard/kitchen-state` (kitchen device registry, add-box forced-manual ids) |
| Blast radius | Cosmetic — a launcher; the underlying services are unaffected |

## Verify

```bash
ssh kdkadmin@10.1.20.20 "sudo docker ps --filter name=homepage --filter name=vikunja --filter name=dashboard-guides --filter name=dashboard-mealplan --format '{{.Names}} {{.Status}}'"
curl -s -o /dev/null -w '%{http_code}\n' https://home.kmkdp.com/            # 200
curl -s -o /dev/null -w '%{http_code}\n' https://home.kmkdp.com/guides/     # 200
curl -s -o /dev/null -w '%{http_code}\n' https://tasks.kmkdp.com/           # 200
curl -s https://home.kmkdp.com/meal/mealplan.json | head -c 200            # JSON (P2)
# kitchen board + household page (through Authelia: check in a signed-in browser, or inside the container)
ssh kdkadmin@10.1.20.20 "sudo docker exec dashboard-mealplan python3 -c \"import urllib.request as u;[print(p, u.urlopen('http://127.0.0.1:8080'+p).status) for p in ('/kitchen','/kitchen/ingredients','/kitchen/steps','/kitchen/pantry','/home','/manifest.webmanifest','/static/home.css')]\""
# kitchen host: 401 unpaired (pair screen), 404 off the allowlist, never an Authelia redirect
curl -s -o /dev/null -w '%{http_code}\n' https://kitchen.kmkdp.com/              # 401 (200 with the device cookie)
curl -s -o /dev/null -w '%{http_code}\n' https://kitchen.kmkdp.com/groceries     # 404
curl -s https://kitchen.kmkdp.com/manifest.webmanifest | head -c 200              # start_url https://kitchen.kmkdp.com/
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
| `stacks/dashboard/mealplan/{kitchen,homecal,vacuum}.py`, `mealplan/static/` | Kitchen board + household page: rendering, ICS calendar expansion, the HA vacuum line, and the shared stylesheet/script/fonts/icons (see below) |
| `stacks/dashboard/mealplan/app.py` | Tandoor session read + rating write-back; groceries snapshot cache (env `GROCERY_TTL_S`, `GROCERY_REFRESH_S`, `RECIPE_TTL_S`); the meal page repeats the planner's Sunday prep lunches as "Lunch prep" on Mon–Sat of that plan week (Tandoor filters meal plans on `to_date` only, so a Sun→Sat entry would drop out of some windows) |
| `stacks/dashboard/mealplan/admin.py` | admin.kmkdp.com: status sources (Alertmanager, Prometheus, Loki, HA, GitHub, Komodo), sign-in parsing, LogQL escaping for the syslog search, sparklines and the page (see [Admin page](#admin-page-adminkmkdpcom)) |
| `stacks/dashboard/mealplan/devices.py` | kitchen.kmkdp.com pairing: device token signing/verifying, the registry, pair codes, rate limit, the pair and devices pages |
| `stacks/dashboard/.env.tpl` | op refs (Vikunja JWT, widget API keys, Tandoor creds, ICS URL, `KITCHEN_DEVICE_KEY`; `KOMODO_API_KEY`/`SECRET` once minted) |

## Kitchen board and household page

Two native pages served by the mealplan sidecar, behind the same Authelia
ForwardAuth (`dashboard-users`) as the rest of `home.kmkdp.com`. No iframes:
everything is rendered server-side with escaped values; `static/home.js` only adds
the clock, ticks, checklists, timers, wake lock, idle return, night view and the
refresh. Homepage at `/` is unchanged (it is retired in a later PR).

| Route | What it is |
|---|---|
| `/meal/kitchen` | Kitchen board for the landscape iPad (~1180×820, fine at 1280×800): clock + date, weather now/high/low + 6-hour strip, calendar line for today and tomorrow; large dinner card (main + side, photo, total time, servings, ingredient and on-hand count); lunch card (this plan week's prep lunches); tomorrow card (dinner + thaw hint); chore columns (every open task, scrolling per column, tick-off) with the vacuum line. Breakfast and snacks are not shown, and nothing personal (no budget, no workouts) |
| `/meal/kitchen/ingredients` | Full-screen checklist for tonight's dinner, scaled to the planned servings, grouped by dish (main, then sides); "on hand" marks pantry/stock foods (planner `pantry:` list, stock stores, or Tandoor's on-hand flag). Ticks live in the browser (`localStorage`, keyed by date) for the evening |
| `/meal/kitchen/steps` | One large step at a time with step tabs and Previous/Next; "This step uses" from the ingredients Tandoor attaches to each step; the step photo; a timer button for every minute/hour value the step text names (chime on the page). `?r=<recipe id>` picks the side dish |
| `/meal/kitchen/pantry` | Pantry check (the **Pantry check** button on the board, and on the household page's dinner card). At the top, a large **Add to list** box for a manual grocery item, with "Added tonight" and the Manual list's current items below it. **Used this week**: the pantry and stock foods this plan week's recipes call for, grouped by store and aisle, each with the recipes it is for. **All pantry staples** (collapsed): the rest of the planner `pantry:` list and the stock-store foods. Each row has a large **Running low** button (or "On the list" when an open entry already exists) and a **Checked** tick that only marks it reviewed tonight (browser `localStorage`, keyed by date); **Mark all checked** ticks a whole section. Built from the groceries snapshot, so a view makes no extra upstream call |
| `/meal/kitchen/step-photo/<file id>` | Step photo proxy (see below) |
| `/meal/home` | Household page, phone-first (390 px, 16 px gutter) and two columns at laptop width: weather, dinner card with its ingredient checklist and a Steps link, lunch prep, tomorrow, chores (at most 8: late first, then due today; **All chores (N)** opens the full board at `?chores=all`), this week's calendar, a compact **Add to list** box at the top (outside the 2-minute refresh, so typing survives it), a **Pantry check** link, and the household app grid (Recipes, Groceries, Tasks, Watch, Listen & read, Photos, Request TV, Request books, Workouts, Budget, Home control, Guides; links from `guides/shared-nav.js`). Recipe titles on all kitchen and household views show without import clutter (" Recipe", "(VIDEO)", "(Quick & Easy)" and similar), with the original in the title attribute; Tandoor is not changed |
| `/meal/kitchen?fragment=1`, `/meal/home?fragment=1` | The swappable page body for the 2-minute refresh |
| `/meal/manifest.webmanifest`, `/meal/static/*` | Home Screen manifest (`display: standalone`, start `/meal/kitchen`), `home.css` (the Bernese tokens), `home.js`, the self-hosted Inter and Space Grotesk fonts (OFL, `static/fonts/`), bear icons |

**Data and caches.** A page view reads caches only; the `home-warmer` thread refreshes
each feed when it goes stale (`HOME_REFRESH_S`, 30 s). A failed refresh keeps the last
good value and is retried after a minute; errors log the exception type only.

| Source | Cache | Upstream call |
|---|---|---|
| Meal plan + recipes | the groceries snapshot (120 s warmer) and the recipe cache (30 min) | Tandoor `/api/meal-plan/`, `/api/recipe/<id>/` (steps, step ingredients, step files) |
| Weather | 15 min, shared with `/meal/weather` | Open-Meteo forecast (keyless) |
| Calendar | 15 min | the Proton ICS feed (`HOMEPAGE_VAR_ICS_URL`, already in `.env`), expanded with `recurring-ical-events` (RRULE, EXDATE, moved occurrences, multi-day all-day events, times shown in America/Chicago). The URL is a secret: it is never logged or rendered |
| Chores | 2 min; a tick re-reads it | Vikunja, every project (jmack token), the `Approvals` project left out |
| Vacuum line | 60 s | Home Assistant `GET /api/states` at `http://10.1.30.21:8123` (`HA_URL`) with `HOMEPAGE_VAR_HA_TOKEN`, filtered to `sensor.rr_room_*_cleaned` (attribute `at`), `input_datetime.rr_next_run`, the water entities (`binary_sensor.mr_slave_water_shortage_2`, `binary_sensor.mr_slave_dock_clean_water_box`, `sensor.mr_slave_dock_dock_error`; "water tank empty" follows the HA `rr_water_empty_alert` rule: dock error `water_empty`, or the box on, or the shortage flag on with the box unchanged since the flag came on, since the flag stays on after a refill), `sensor.rr_vacuum_error` and `sensor.mr_slave_*_time_left*` (negative = due). Needs the `allow_ctr dashboard-mealplan 10.1.30.21 tcp 8123` line in `hosts/kdk-dkr-01/kdk-docker-user.conf`; without it the line is simply missing |

**Step photos.** Tandoor serves a step's UserFile at `/api/download-file/<id>/` only to a
logged-in session (and wraps it in a one-file zip). The proxy serves only file ids attached
to a step of a recipe in the meal-plan snapshot (this plan week to two weeks out); anything
else is a 404 without a Tandoor call. It unwraps the zip, sends only real images
(JPEG/PNG/WebP/GIF by magic bytes), and caches up to 80 photos for a day.

**Ticks.** A chore tick posts `ajax=1` to `/meal/tasks/complete`, which completes the task with
the viewer's Vikunja token (else jmack's), re-reads the board and answers 204; the page then
swaps in the fresh board, so a recurring chore comes back with its next date. Without
JavaScript the form posts and redirects as before.

**Writes from the kitchen pages.** The kitchen tablet can tick chores (above), add pantry
staples to the shopping list, and add manual grocery items. Nothing else is written: no
Tandoor on-hand (`food_onhand`) edits, no recipe or meal-plan changes. Both grocery writes
use the groceries page's own routes, with `ajax=1` (a JSON answer) and a fixed `next` key
(`pantry`, `pantry-home`, `home`; anything else falls back to the groceries page, never a
URL) for the plain-form fallback:

| Write | Route | Behaviour |
|---|---|---|
| Running low | `POST /meal/groceries/reorder` | ONE recipe-less shopping entry (amount 1, the `foods.yaml` `buy:` unit if any) for a pantry or stock food; any other food is refused (422). Idempotent: the duplicate check reads the list live and a lock holds the check and the write together, so a second tap, or the tablet and a phone at once, answers `on_list` without a new entry. The button comes back once the entry is ticked off |
| Add to list | `POST /meal/groceries/add` | The add box (pantry check, household page, groceries Manual tab): trimmed, empty refused (422), capped at 80 characters, a recipe-less entry by name (amount 1). See "The add box" below for where it lands |

**The add box.** Tandoor reuses an existing food by name, so a **known food lands on its
store list** (NewPi or Target, by the food's supermarket category, the same
`_split_groceries` rule as every list on the page) and an **unknown name on the Manual
list**. The answer says where it went: "Added to New Pi · Dairy & Eggs", "Added to Target ·
Pantry & Grains", "Added to Manual list" (pet food: "stays off the store lists"). It comes
from the created entry in Tandoor's POST answer (food + `supermarket_category`), or the
re-read list if the answer lacks it. The ajax answer is
`{status, item, store, aisle, text}` (`store` = `NewPi`, `Target`, `Manual`, `Off`, or
empty if unknown); the plain-form fallback carries it in one fixed query key,
`?added=<store>|<aisle>|<item>` (URL-encoded, re-validated and HTML-escaped when shown).
The pantry and household views list each add under "Added tonight" with its destination
(home.js, browser `localStorage` for the evening); the groceries page shows a flash line
under the Manual tab's box.

**Manual list instead.** A checkbox next to the add button (44 px target, off by default,
back to off after each add) keeps the item on the Manual list even when the food is known,
so it stays out of the New Pi and Target tabs. Tandoor is not changed (no food or category
edit): the new entry's id goes into `/state/manual-forced.json`
(`/opt/kdk-lab/dashboard/kitchen-state/manual-forced.json`, the same bind mount as the
device registry; written atomically, mode 600, `{"ids": {"<entry id>": <added at>}}`).
`_split_groceries` puts those ids on the Manual list. Every read prunes ids whose entry is
checked, or is gone from the list and older than 30 minutes (a page may render from a
snapshot read before the add), so the file only holds the few open forced items. Deleting
it only sends those items back to their store lists. If `/state` is not writable the box
shows the toggle disabled with a short note, and normal routing keeps working.

**Outside the dashboard.** The page's tabs, counts, the pantry view's Manual list and the
Target search links all use `_split_groceries`; `/mealplan.json` is the meal plan only.
Two readers go to Tandoor directly and do **not** know the registry: the grocery-planner's
weekly run (`build_store_lists`, Tue 18:03) counts an open forced item under its store in
the approval task's "New Pi N items, Target N items", and `scripts/newpi-order.py` (a
workstation dry run; `--cart` is not built) lists every unchecked entry whatever its
category. Neither adds to a store cart, and the Thursday poll only deletes entries linked to
that week's meal plan, never an add-box entry. Ordering is done from the page, so a
forced-manual item is not ordered.

**Kitchen behaviour.** Screen Wake Lock, re-requested on `visibilitychange` (iPadOS 18.4+
in a Home Screen app). The sub-views (ingredients, steps, pantry check) return to the board after 5 idle minutes (not while a
timer runs). The board refreshes every 2 minutes and on wake without a page flash, keeping
each column's scroll. 22:00–06:00 Chicago time the board turns into a near-black night view
(dim clock and tomorrow's first event); a tap shows the board for 3 minutes. The layout
moves by up to 3 px every hour against LCD ghosting. Targets are at least 44 px.

**Tests** (fixtures only, no network), from `stacks/dashboard/mealplan`:

```bash
uv run --no-project --with flask==3.0.3 --with requests==2.32.3 --with waitress==3.0.1 \
  --with pyyaml==6.0.3 --with icalendar==7.3.0 --with recurring-ical-events==3.8.2 \
  --with x-wr-timezone==2.0.1 --with python-dateutil==2.9.0.post0 --with tzdata==2026.5 \
  --with segno==1.6.6 --with pytest python -m pytest -q tests
```

### Runbook: the kitchen iPad as a Home Screen app

**On a laptop or phone signed in as jmack**

1. Open `https://home.kmkdp.com/meal/kitchen/devices` → **Pair a device**. The page
   shows a link, a QR code and the code; it works once, for 10 minutes.

**On the kitchen iPad (iPadOS 18.4 or later), in Safari**

2. Scan the QR code with the Camera (or type the link), check the device name
   (**Kitchen iPad**) and press **Pair**. The kitchen board opens at
   `https://kitchen.kmkdp.com/` with no sign-in.
3. Still on `kitchen.kmkdp.com`: Share → **Add to Home Screen** → name it **Kitchen**
   → Add. The bear icon appears; the app opens `https://kitchen.kmkdp.com/`.
4. Open **Kitchen** from the Home Screen. A Home Screen app can keep its own cookie
   jar: if it shows **This screen isn't paired**, make a new code on the devices page
   and type it into the box there (the code groups are not case-sensitive).
5. Settings → Display & Brightness → **Auto-Lock → Never** (the page also holds a
   wake lock while it is open, and turns into the night view 22:00–06:00).
6. Check: the clock ticks, a chore tick strikes through and the board comes back with
   it gone, **Steps** shows a timer that chimes (the first tap on a timer unlocks
   audio), and the devices page shows the iPad with a fresh **Last seen**.

To update the app after a deploy nothing is needed: the page fetches fresh HTML every
time, and `home.css`/`home.js` carry a content hash. The cookie lasts one year; pair
again before then (the devices page shows the paired date). **Lost or replaced iPad:**
**Revoke** it on the devices page (or **Revoke all**), then pair the new one. To start
over on the same iPad, long-press the icon → Remove App, revoke the old entry, and
repeat the steps.

## Kitchen host and device pairing (kitchen.kmkdp.com)

The kitchen screen opens `https://kitchen.kmkdp.com/` instead of the
`home.kmkdp.com` pages, so it never needs an Authelia sign-in. Authelia's
`remember_me` is global (1 month for every account) and is not raised for this;
**the `home.kmkdp.com` Authelia rule is unchanged** (two_factor, `dashboard-users`).

**Routing.** A second router on the `mealplan` sidecar, on the internal Traefik
on kdk-dkr-01 only: LAN and Tailscale, never the Cloudflare tunnel or the DMZ.
It has **no forwardauth**. Its `kitchen-headers` middleware deletes any
client-sent `Remote-User`, `Remote-Name`, `Remote-Email` and `Remote-Groups`
header and sets `X-Kdk-Kitchen: 1`. The sidecar treats a request as the kitchen
host when the Host is `kitchen.kmkdp.com` or that header is set, removes the
Remote-* headers again (other containers on the `proxy` network can reach
`:8080` directly), and ignores them for ratings and ticks.

**DNS and certificate.** Nothing by hand. dns-reconcile (gitops-sync) reads the
`Host()` label and adds the AdGuard rewrite `kitchen.kmkdp.com → 10.1.20.20`
(stack server kdk-dkr-01); until then the `*.kmkdp.com` wildcard rewrite already
answers 10.1.20.20. The `websecure` entrypoint's Let's Encrypt wildcard
(`*.kmkdp.com`) covers the name. There is no public DNS record and no tunnel
hostname; do not add one.

**What the kitchen host serves.** Everything else answers 404 there.

| Path | Needs |
|---|---|
| `GET /`, `/?fragment=1`, `/ingredients`, `/steps`, `/pantry`, `/step-photo/<id>` | device cookie |
| `POST /tasks/complete`, `/groceries/reorder`, `/groceries/add` (the existing handlers) | device cookie + `Origin` (or `Referer`) `https://kitchen.kmkdp.com`; with `ajax=1` also `X-Kitchen: 1` (home.js sends it). A plain form post relies on the Origin check plus `SameSite=Strict` |
| `GET /manifest.webmanifest` (`start_url` and `scope` `https://kitchen.kmkdp.com/`), `GET /static/*` | nothing (not secret; the Home Screen icon fetch has no cookie) |
| `GET /pair` (the form), `POST /pair` | a pairing code in the POST body; POSTs rate-limited to 10 tries per 10 minutes per client IP (the last `X-Forwarded-For` hop, set by Traefik) |

An unpaired screen gets a 401 page with a box for the pairing code. If
`KITCHEN_DEVICE_KEY` is missing (or shorter than 32 bytes), or `/state` is not
writable, every kitchen-host path answers **503 "kitchen pairing not
configured"**; `home.kmkdp.com` keeps working. `home.kmkdp.com/meal/kitchen`
still works for signed-in users.

**The device cookie.** `kitchen_device` = `k1.<key id>.<device id>.<issued>.<expires>.<HMAC-SHA256>`,
signed with `KITCHEN_DEVICE_KEY`, valid one year, `HttpOnly; Secure; SameSite=Strict; Path=/`,
host-only (no `Domain`, so no other `*.kmkdp.com` host ever sees it). The signature
is compared with `hmac.compare_digest`, and a good signature is not enough: the
device must be in the registry, not revoked, and the issued time must match.
The key id is a hash of the key, so **rotating the key revokes every device**.

**The registry.** `/opt/kdk-lab/dashboard/kitchen-state/devices.json` (bind
mount `/state`, written atomically, mode 600): id, name, issued, expires,
last_seen (written at most every 5 minutes), revoked, plus the open pairing
codes as SHA-256 hashes. Losing it only means re-pairing the screen. The same
mount holds `manual-forced.json`, the add box's "Manual list instead" ids (see
"The add box" above).

**Pairing and revoking** (`home.kmkdp.com/meal/kitchen/devices`, behind the usual
Authelia rule, and only for `KITCHEN_ADMINS`, default `jmack`; comma-separated):

- **Pair a device** makes a one-time code: 128 random bits (26 base32
  characters), valid 10 minutes, single use, stored hashed. The page shows the link
  `https://kitchen.kmkdp.com/pair#code=…`, a QR code of it (segno, pinned) and the
  code in groups of four for typing.
- The code rides in the URL **fragment**, which a browser never sends to a server,
  so it never appears in Traefik's access log or Loki. The `/pair` page script
  copies it into the code box, clears the fragment (`history.replaceState`) and
  focuses the device name; without JavaScript, type the code into the box. A
  `?code=` query is ignored (not read, not logged).
- **Pair** posts the code and name, uses up the code, sets the cookie and goes to
  `/`. The code reaches the server only in that POST body; opening the link uses
  nothing up, so a link preview cannot burn it.
- The list shows each device with paired and last-seen times, a **Revoke** button
  per device and **Revoke all**. A revoked screen gets the pairing page on its next
  request.
- The sidecar logs `kitchen: pairing code issued`, `device paired`, `device
  revoked`, `all devices revoked`, refused cookies, refused POSTs and rate limits,
  with the device id and client IP but never the code or the token:
  `{host="kdk-dkr-01",container="dashboard-mealplan"} |= "kitchen:"`.

**Chore ticks from the kitchen host** use `VIKUNJA_TOKEN_KITCHEN` (a Vikunja
"Kitchen" account) when it is set, so Vikunja credits the tick to Kitchen. Until
then they use the jmack token, and the sidecar logs once at start
`VIKUNJA_TOKEN_KITCHEN not set: … (fallback active)`.

### Hand step: the signing key (before the PR merges)

`op inject` fails, and the stack does not deploy, if the item is missing. Laptop,
`op` signed in:

```sh
RECIPE=letters,digits,64
op item create --vault kdk-cluster --category password \
  --title dashboard-kitchen-device-key --generate-password=$RECIPE
op read op://kdk-cluster/dashboard-kitchen-device-key/password | tr -d '\n' | wc -c   # 64
```

The host copy comes from `op` too: the Komodo `pre_deploy` renders it into
`stacks/dashboard/.env` with `op inject` on every deploy. Check it after the deploy
(length only):

```sh
ssh kdkadmin@10.1.20.20 "sudo -n sh -c 'grep ^KITCHEN_DEVICE_KEY= /opt/kdk-lab/stacks/dashboard/stacks/dashboard/.env | cut -d= -f2- | tr -d \"\\n\" | wc -c'"   # 64
```

**Rotate** (revokes every paired screen; re-pair afterwards):
`op item edit dashboard-kitchen-device-key --vault kdk-cluster --generate-password=$RECIPE`
(same `RECIPE`), then redeploy the dashboard stack (`env_file` is only read on a
recreate; the changed `.env` recreates `dashboard-mealplan`).

### Hand step: the Vikunja "Kitchen" account (optional, any time)

Vikunja logs in through Authelia OIDC only (`two_factor`), so Kitchen is a directory user.

1. 1Password first, laptop:
   `op item create --vault kdk-cluster --category login --title lldap-kitchen username=kitchen --generate-password=letters,digits,symbols,32`
2. `users.kmkdp.com` → Create user `kitchen`, display name **Kitchen**, an email
   you read; set its password from `op read op://kdk-cluster/lldap-kitchen/password`
   (paste it, do not print it in a shared terminal). No groups are needed.
3. In a private window, sign in to `https://tasks.kmkdp.com` with **Authelia** as
   `kitchen` and register a TOTP when Authelia asks; save its `otpauth://` URI in
   the same item (`op item edit lldap-kitchen --vault kdk-cluster` with an `[otp]`
   field). That first login creates the Vikunja user.
4. As jmack in Vikunja: share each chore project (not `Approvals`) with user
   `kitchen`, permission **Read & write**.
5. As kitchen: Settings → API Tokens → new token **kitchen-ticks**, permissions
   `tasks`: read one + update, expiry 1 year. Copy it once, then on the laptop:
   ```sh
   read -rs VTOK
   op item create --vault kdk-cluster --category 'API Credential' --title vikunja-api-kitchen "credential[password]=$VTOK"
   unset VTOK
   op read op://kdk-cluster/vikunja-api-kitchen/credential | tr -d '\n' | wc -c   # > 0
   ```
6. A small PR adds the real line
   `VIKUNJA_TOKEN_KITCHEN={{ op://kdk-cluster/vikunja-api-kitchen/credential }}` to
   `stacks/dashboard/.env.tpl` (only once the item exists). After its deploy the
   start log says `chore ticks use the Kitchen Vikunja account`, and a tick from the
   kitchen screen shows Kitchen as the doer in Vikunja.

## Admin page (admin.kmkdp.com)

The lab admin page for jmack: live status, firing alerts, recent sign-ins, a
syslog search, six sparklines and the admin service tiles that are not on the
household page. It is a native page from the `mealplan` sidecar (`admin.py`),
not Homepage, and it is **read-only**: it has no forms that write and no POST
route, and it runs no JavaScript.

**Routing and access.** A third router on the `mealplan` sidecar, on the
internal Traefik on kdk-dkr-01 only (LAN and Tailscale, never the tunnel),
**with** `forwardauth@file`. Three gates, in order:

1. Authelia rule `admin.kmkdp.com` → `two_factor`, subject `user:jmack` (there
   is no admins group). It sits above the `home.kmkdp.com` rule in
   `stacks/identity/authelia/configuration.yml`.
2. The sidecar checks `Remote-User` against `ADMIN_USERS` (default `jmack`,
   comma-separated) and answers **403** to anyone else, on every path.
3. On that host it serves only `GET /` (the page; the syslog search is the same
   page with `?q=&host=&range=`) and `GET /static/*`. Every other path and every
   POST is a **404**, and the page exists on no other host: `home.kmkdp.com` and
   `kitchen.kmkdp.com` never serve it (the kitchen marker wins if both match).

Responses on the admin host carry `Cache-Control: no-store`, a strict CSP
(`default-src 'none'`; styles, images and fonts from self only; no scripts),
`X-Frame-Options: DENY`, `nosniff` and `Referrer-Policy: same-origin`. The page
reloads itself every 60 s with a meta refresh, except on a search result.

The household page (`/meal/home`) shows an **Admin** link in its header only to
`ADMIN_USERS`.

DNS and certificate need nothing by hand: dns-reconcile adds the AdGuard rewrite
`admin.kmkdp.com → 10.1.20.20` from the `Host()` label (the `*.kmkdp.com`
wildcard rewrite answers until then), and the `*.kmkdp.com` Let's Encrypt
wildcard covers the name. No public DNS record, no tunnel hostname.

**Sources and caches.** Every source is a server-side `Feed` (the same cache the
kitchen board uses) refreshed by an `admin-warmer` thread (`ADMIN_REFRESH_S`,
default 30 s; 0 = off). A page view only reads the caches: a source that has not
answered yet shows "loading", a failed one shows "unreachable" and keeps its
last good value when it had one. The browser never talks to any of them.

| Tile / panel | Source | Cache |
|---|---|---|
| Alerts firing + the alert list | Alertmanager `GET /api/v2/alerts?active=true&silenced=false&inhibited=false` on `10.1.20.30:9093` | 30 s |
| Services down + the tile dots | Prometheus `probe_success` and `up{job!~"blackbox-.*"}` on `10.1.20.30:9090` | 60 s |
| Backups | Loki: the backup-* one-shots' `event name=backup-… action=die code=N` lines from docker-event-watch (the Grafana "Backups" dashboard's source), last 26 h, latest exit per container | 5 min |
| Deploys failed | Komodo `POST /read` `ListUpdates` (updates of the last 24 h; failed = a finished `*Deploy*` update with `success: false`). Shows **"not configured"** until `KOMODO_API_KEY`/`KOMODO_API_SECRET` exist (hand step below) | 5 min |
| Draft PRs | GitHub `GET /repos/BigH3ater/kdk-lab-dkr/pulls?state=open` (public, **no token**; anonymous limit 60/h, so 15 min cache and a 5 min retry) | 15 min |
| Updates | open Renovate PRs (same GitHub call) + `vulnerable=` from advisory-watch's weekly `summary=1` line in Loki (`{job="advisory-watch"}`, last 8 days); "to review" = its `unknown=` | 15 min |
| UPS | Home Assistant NUT sensors `sensor.computer_room_eaton_5px_*` (status flags, load, charge; runtime when HA has a runtime sensor), with the existing dashboard HA token. Prometheus has **no NUT exporter**, so HA is the source | 60 s |
| Recent sign-ins | Loki `{container="authelia"} |~ "Unsuccessful|getting details for user|requires 2FA"`, last 24 h | 5 min |
| Sparklines (24 h, 15 min steps) | Prometheus `query_range`: lab-wide CPU busy and memory used, the fullest root disk, the `tankz3` pool on kdk-nas-01, NIC in+out on all lab hosts; UPS load from HA history (averaged per 15 min) | 5 min |
| Syslog host list | Loki `host` label values for `{job="syslog"}` (fallback: the 10 known hosts) | 1 h |

**Sign-ins: what Authelia logs.** At `log.level: info` Authelia 4.39 writes a
line for a failed first factor (`Unsuccessful 1FA … by user 'x'`), an unknown
username (`… username input 'x' …`), a failed or banned 2FA, and an accepted
first factor only while 2FA is still due (`<url> requires 2FA, cannot be
redirected yet`, which has no username, or `OpenID Connect 1.0 client requires
2FA`, which has the username and client id). A finished 2FA and a remembered
session write nothing. So the panel lists sign-in **attempts**: failures with the
user, and first factors with the app (and the user for OIDC). The edge probe's
`edge-probe-nonexistent` attempts are hidden and counted. The complete record is
`authentication_logs` in authelia-postgres. Client IPs are not shown.

**Syslog search.** A text box, a host select and a 1 h / 6 h / 24 h range. The
sidecar builds `{job="syslog"[, host="<host>"]} |~ "(?i)<text>"`:

- the text is escaped twice: every RE2 metacharacter gets a backslash (so it
  matches literally, case-insensitively), then the whole regex goes into one
  LogQL double-quoted string with `\` and `"` escaped. Control characters and
  more than 200 characters are refused. The text can never reach a label matcher
  or a pipeline stage (tests cover `"} or {job=~".+` and friends);
- the host must be one of the Loki `host` values (and `^[a-z0-9][a-z0-9.-]*$`);
- the range is one of the three values;
- Loki `query_range`, newest first, **200 lines max**, 10 s timeout; 30 searches
  per 10 minutes per user. The log line records user, host, range, the text
  length and the line count, never the text;
- ANSI colour codes (`#033[…m`) are stripped for display; every line is
  HTML-escaped.

"Open in Grafana Explore" opens the same query (Loki datasource `loki-dkr01`)
for the full set.

**Service tiles.** Eight groups (Media ops, Books, Home automation, Identity,
Deploy, Infrastructure, Monitoring, Household data), hrefs from
`config/services.yaml` (a test keeps them in sync). A green or red dot means a
blackbox probe exists for that host name (or, for the IP tiles, an ICMP probe
of that IP); no dot means no probe.

**Network.** The sidecar needs Prometheus and Alertmanager on kdk-mon-01, which
the dkr-01 DOCKER-USER policy blocks: `allow_ctr dashboard-mealplan 10.1.20.30
tcp 9090,9093` in `hosts/kdk-dkr-01/kdk-docker-user.conf` (host step below).
Loki on kdk-log-01 `:3100` is already open to every container (the Alloy
`allow_dst` line). Home Assistant `:8123` has its own line (vacuum). GitHub and
`komodo.kmkdp.com` were reachable from the container on 2026-11-10; Prometheus
and Alertmanager were not (connect timeout), as expected before the host step.

**Settings** (compose `environment`, all optional): `ADMIN_USERS`, `ADMIN_HOST`,
`ADMIN_REFRESH_S`, `PROM_URL`, `ALERTMANAGER_URL`, `ALERTMANAGER_UI`, `LOKI_URL`,
`GRAFANA_UI`, `HA_UI`, `KOMODO_URL`, `UPS_ENTITY_PREFIX`, `NAS_INSTANCE`,
`NAS_POOL`.

### Host step: the DOCKER-USER exception (after the PR merges)

The policy file is applied by hand (no gitops for host files). From the repo root
on the laptop, after `git pull`:

```sh
ssh kdkadmin@10.1.20.20 'sudo install -m 644 /dev/stdin /etc/default/kdk-docker-user && sudo systemctl start kdk-docker-user.service' < hosts/kdk-dkr-01/kdk-docker-user.conf
ssh kdkadmin@10.1.20.20 'sudo iptables -S DOCKER-USER | grep -c "10.1.20.30.*9090,9093"'   # 1
ssh kdkadmin@10.1.20.20 "sudo docker exec dashboard-mealplan python3 -c \"import urllib.request as u;[print(p, u.urlopen('http://10.1.20.30'+p, timeout=4).status) for p in (':9090/-/ready', ':9093/-/ready')]\""   # 200 200
```

The rule follows the container's address (the timer refreshes it every 5
minutes and on every Docker start), so a redeploy needs no new step.

### Hand step: the Komodo read key (optional, any time)

Until this is done the "Deploys failed" tile says "not configured"; nothing
else depends on it.

Done 2026-11-10: the item `kdk-cluster/komodo-api-ro` (fields `key`, `secret`) holds the
key of a non-admin **service user** (verified: `ListUsers` is refused as admin-only, while
`ListUpdates` and `ListStacks` read fine), and `.env.tpl` injects it. To redo it:

1. Komodo UI → **Settings → Users → New Service User** (not the sign-up page: that
   replaces your session). Not admin, no create permissions; **Read** on the resource
   types whose deploys you want counted (stacks, procedures).
2. On that user's page, **API Keys → New** (`dashboard-admin-page`, with an expiry).
   Copy the key and secret straight into the item's `key` and `secret` fields, never
   into a file or chat. Check by length:

   ```sh
   op read op://kdk-cluster/komodo-api-ro/key | tr -d '\n' | wc -c      # 44
   op read op://kdk-cluster/komodo-api-ro/secret | tr -d '\n' | wc -c   # 44
   ```

3. Never use an admin user's key here: the page only reads, and an admin key could deploy.
4. Check: the tile shows "N of M in 24 h". The ListUpdates call was written
   against the Komodo client docs and has not run against the live Core yet
   (no key existed); if the tile says "Komodo unreachable", look at
   `docker logs dashboard-mealplan | grep admin-komodo`.

### Verify (after merge, the Authelia restart and the host step)

```sh
curl -s -o /dev/null -w '%{http_code}\n' https://admin.kmkdp.com/        # 302 or 401 from Authelia (not signed in), never the page
# signed in as jmack in a browser: the page, 7 tiles, no "unreachable"
# signed in as rach: Authelia 403 (the rule), never the page
ssh kdkadmin@10.1.20.20 "sudo docker exec dashboard-mealplan python3 -c \"import http.client as h
for user in ('jmack', 'rach'):
    c = h.HTTPConnection('127.0.0.1', 8080); c.request('GET', '/', headers={'Host': 'admin.kmkdp.com', 'Remote-User': user})
    print(user, c.getresponse().status)\""                                  # jmack 200, rach 403
ssh kdkadmin@10.1.20.20 'sudo docker logs dashboard-mealplan --since 10m 2>&1 | grep -E "feed admin-|admin:"'   # no refresh failures
```

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
redeploy. `dashboard-kitchen-device-key` rotation revokes every paired kitchen screen (see
[the signing key](#hand-step-the-signing-key-before-the-pr-merges)). The `dashboard-tandoor-rw` account password rotates in Tandoor + op. Vikunja JWT
rotation logs everyone out (harmless; they re-login).

## User Guide

The dashboard *is* the user guide: `home.kmkdp.com` → **How-To Guides** (`/guides/`) covers
recipes, watching, requests, audiobooks, books, photos, and budget in plain language.
