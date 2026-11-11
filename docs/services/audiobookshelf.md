---
title: Audiobookshelf
host: kdk-dkr-dmz-01
stack: audiobookshelf
tier: dmz
status: live
storage: yes
secrets: []
updated: 2026-11-10
tags: [homelab/stack, tier/dmz, status/live]
---

# Audiobookshelf

Audiobook, ebook and podcast server on the DMZ node. Its router serves the DMZ Traefik's `websecure` and `tunnel` entrypoints, but it stays LAN/Tailscale only until the owner adds the tunnel public hostname ([Publishing](#publishing)). Login is ABS's own auth or Authelia OIDC (client `audiobookshelf`), no ForwardAuth (it would break the mobile apps). Registered mobile OIDC schemes (ABS Settings → Authentication → Allowed Mobile Redirect URIs, `authOpenIDMobileRedirectURIs`, a live setting held in the ABS database, not in git): `audiobookshelf://oauth`, `prologue://oauth`, `plappa://oauth`. ABS checks the app scheme itself and gives Authelia only its own `https://audiobookshelf.kmkdp.com/auth/openid/mobile-redirect` callback, so a new app needs only an ABS entry, not an Authelia change.

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]] · [[identity]] · [[media]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Audiobooks and ebooks (LazyLibrarian-managed, read-only) and podcasts (RSS auto-download) |
| Host | [[kdk-dkr-dmz-01]] |
| Endpoint | `https://audiobookshelf.kmkdp.com` (native `/audiobookshelf` base path) |
| Image | `ghcr.io/advplyr/audiobookshelf:2.37.2` (digest-pinned in `stacks/audiobookshelf/compose.yaml`) |
| Deploy | Komodo stack `audiobookshelf` (git-linked) |
| Storage | config + metadata (SQLite DB) local in `/opt/kdk-lab/audiobookshelf`; audiobooks RO from `/mnt/media/books/audiobook`; ebooks RO from `/mnt/media/books/ebook`; podcasts rw on NFS `/mnt/appdata/audiobookshelf/podcasts` |
| Blast radius | No audiobook/podcast streaming or phone/browser ebook reading; progress sync pauses. The reMarkable can still open books already downloaded, but cannot fetch new ones (its OPDS catalogue, [Reading sync](reading-sync.md), reads from ABS) |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://audiobookshelf.kmkdp.com/healthcheck   # 200
ssh kdkadmin@192.168.191.20 'ls /mnt/appdata/audiobookshelf/podcasts | head'          # podcast folders
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| New podcast episodes fail to download | `/mnt/appdata` NFS not mounted or not writable by the container user | `findmnt /mnt/appdata`; NAS dir is `1000:1000` (works as root today and as uid 1000 after hardening) |
| New LazyLibrarian audiobooks never appear in the library | the library watcher does not fire for writes made on the NFS **server** (LazyLibrarian writes `/data/media/books/audiobook` on kdk-dkr-01 → the same `tankz3/media` dataset ABS mounts read-only); inotify events are not delivered to dmz-01, and the `audiobooks` library has no scheduled scan (`autoScanCronExpression` unset) | set a scan schedule on the library (Settings → Libraries → audiobooks → Schedule automatic scans) or trigger a manual scan / `POST /api/libraries/<id>/scan` after an import; a server restart also re-scans |
| Client assets 404 at the bare domain | base path changed | keep `ROUTER_BASE_PATH=/audiobookshelf` (see compose comment) |

## Configuration

| Path / setting | What it is |
|---|---|
| `/opt/kdk-lab/audiobookshelf/config` | SQLite DB, users, settings (local disk, nightly backup-dmz) |
| `/opt/kdk-lab/audiobookshelf/metadata` | covers, cache (local disk) |
| `/mnt/media/books/audiobook` → `/audiobooks:ro` | audiobook library (media NFS is read-only on the DMZ) |
| `/mnt/media/books/ebook` → `/ebooks:ro` | `ebooks` library, the only ebook library (LazyLibrarian writes it on kdk-dkr-01); ABS must never write it |
| `/mnt/appdata/audiobookshelf/podcasts` → `/podcasts` | podcast library, written by ABS. Moved off the 59 GB root disk 2026-10-28 (7.5 GB). Library path in ABS stays `/podcasts`. Not in backup-dmz (tankz3 snapshots only; re-downloadable) |

## Observability

Loki rules (`stacks/loki/rules/`), runbook [security alerts → mobile apps](../runbooks/security-alerts.md#mobile-apps):

| Alert | File | Severity | Fires on |
|---|---|---|---|
| `AbsLoginFailures` | `mobile-apps.yml` | warning | more than 5 `[LocalAuth] Failed login attempt` lines in 10m |
| `AbsNewUserOrApiKey` | `mobile-apps.yml` | warning | `[ApiKeyController] Created API key` or `[User] openid: Auto-registering user` |
| `AbsLoginFromInternet` | `mobile-apps.yml` | info, `notify: phone` | tunnel `POST /login` 200 or OIDC callback 200/302 |
| `AbsMassDownload` | `mobile-apps.yml` | warning | more than 10 item/file/library download requests from one client in 15m |
| `EdgeAbsAdminPathHit` | `edge.yml` | critical | a non-probe admin API request reached Traefik through the tunnel |
| `EdgeAbsAdminPathProbe` | `edge.yml` | warning | the edge probe's own admin row reached Traefik (rule 1 is missing a path) |

`scripts/edge-probe.sh` has ABS rows (client paths, admin 403s, `/login` + `/api/authorize` rate-limit bursts); they SKIP until the hostname has a public A record.

## Publishing

Repo side is done (router `entrypoints: websecure,tunnel`, `abs-headers` middleware, the rules above, edge-probe rows, `TUNNEL_HOSTS` in cloudflare-watch). Source: vault `10.Homelab/Security/audiobookshelf-public-readiness-2026-11-08.md` (verdict GO-with-conditions for audiobooks + podcasts). Do the steps in order; the public hostname is the last one.

### Pre-publish hand steps (owner)

1. **C1 — legacy tokens and API keys (ABS UI, from the LAN).** Settings → Users → each of `kdkadmin`, `jmack`, `admin` → *Regenerate API token* (invalidates the never-expiring legacy `users.token`). Settings → API keys → give `readarr` and `kdk-cluster` an expiry (recreate them with one if the UI cannot edit it; a key's value is shown once). Move `abs-notify` onto an API key **in the same step**, on the laptop:

   ```bash
   op item edit audiobookshelf-api-root --vault kdk-ops 'credential=<the new API key>'
   op read op://kdk-ops/audiobookshelf-api-root/credential | \
     ssh kdkadmin@10.1.20.20 'sudo tee /etc/kdk/abs-notify.env >/dev/null <<<"ABS_API_TOKEN=$(cat)" && sudo chmod 600 /etc/kdk/abs-notify.env && sudo chown root:root /etc/kdk/abs-notify.env'
   ```

   `env_file` is read at compose time, so redeploy the `media` stack (it recreates `lazylibrarian`) and check the next import's rescan (`docker logs lazylibrarian | grep abs-notify`). Update the 1Password item of the `kdk-cluster` key's consumer the same way.
2. **C2 — the `admin` OIDC account.** Settings → Users → `admin`: demote to `user` or delete it (second admin-role account, no password, unused since 2026-09-18; OIDC auto-register is on).
3. **Host tidy (kdk-dkr-dmz-01).** DB mode 600 and the stale backup gone:

   ```bash
   sudo chmod 600 /opt/kdk-lab/audiobookshelf/config/absdatabase.sqlite
   sudo rm /opt/kdk-lab/audiobookshelf/config/absdatabase.sqlite.bak.20260923
   ```

### Cloudflare edits (owner, dashboard)

4. **C3 — Rule 1 "Block admin APIs"** (Security → Security rules). Replace the ABS branch with the compact form:

   ```
   or (http.host eq "audiobookshelf.kmkdp.com" and (
     (lower(http.request.uri.path) contains "/api/" and (lower(http.request.uri.path) contains "/users" or lower(http.request.uri.path) contains "/backups" or lower(http.request.uri.path) contains "/filesystem" or lower(http.request.uri.path) contains "/api-keys" or lower(http.request.uri.path) contains "/settings" or lower(http.request.uri.path) contains "/tools" or lower(http.request.uri.path) contains "/cache" or lower(http.request.uri.path) contains "/emails" or lower(http.request.uri.path) contains "/notifications" or lower(http.request.uri.path) contains "/share" or lower(http.request.uri.path) contains "/upload" or lower(http.request.uri.path) contains "/custom-metadata"))
     or (http.request.method ne "GET" and lower(http.request.uri.path) contains "/api/libraries")
   ))
   ```

   Byte budget: the deployed rule is 3893 B of the 4096 B limit; the long one-path-per-line form would land at ~4180 B (rejected). The compact form is about +150 B → ~4040 B, ~50 B spare (the editor shows the count). If it is still rejected, drop `/upload` and `/custom-metadata` first (admin-only, LAN use). `/settings` also covers `/auth-settings`.
5. **Rate-limit rule** ("Ratelimit Jellyfin /users"): add to the `in {…}` set

   ```
   "/login" "/audiobookshelf/login" "/api/authorize" "/audiobookshelf/api/authorize" "/auth/refresh" "/audiobookshelf/auth/refresh"
   ```

   and the line

   ```
   or starts_with(lower(http.request.uri.path), "/auth/openid")
   ```

   (covers `/auth/openid`, `/auth/openid/callback`, `/auth/openid/mobile-redirect`). ABS has no lockout and its own limiter is 40 / 10 min per IP: the edge rule is the real brake.
6. **Transform rule** "Strip client X-Forwarded-For": add `"audiobookshelf.kmkdp.com"` to the host set. Cosmetic since `cf-realip@file` on the `tunnel` entrypoint does the real work; keep the convention (every tunnel hostname is in the set).
7. **Baseline:** `scripts/edge-probe.sh --no-ratelimit`: ABS rows SKIP, every other row PASS.

### Publish

8. Zero Trust → Networks → Tunnels → `cloudflare-tunnel-credentials` → Public hostname → Add `audiobookshelf.kmkdp.com` → service **HTTPS** `traefik:8443` → TLS → **Origin Server Name** `audiobookshelf.kmkdp.com` (the convention for every tunnel hostname: without it the origin TLS name is `traefik` and the edge answers 502/530), No TLS Verify off. The proxied CNAME appears with it.
9. **Post-publish probes:** `scripts/edge-probe.sh` (with the rate-limit bursts) → ABS rows PASS: `/ping`, `/status` 200, `/login` 200/301, `/api/authorize` 401, the nine admin paths 403, and a 429 in both burst tails (the `/login` burst fires `AbsLoginFailures` once; expected). One real login per platform from off Wi-Fi → `AbsLoginFromInternet` pings the phone. Then check Security → Events for unexpected 403s.

**Rollback:** delete the public hostname (the CNAME goes with it): edge 530 within a minute. The router label can stay (the `tunnel` entrypoint only receives what cloudflared sends) or be reverted to `websecure`; the Cloudflare rule edits are inert without the host.

## Apps and ebooks

- Which phone apps to use (iOS/Android, audio vs ebooks, OIDC schemes): vault `3. Resources/Reading/ABS Apps and Ebooks 2026-11-08.md`.
- **All ebooks live here.** Phones and browsers read them in ABS; the reMarkable's KOReader pulls them over OPDS through abs-opds ([Reading sync](reading-sync.md)). Calibre was retired 2026-11.
- **`/ebooks` is its own mount**, so the audiobook library keeps its `/audiobooks` path and its items and progress are not re-matched.
- **New books appear within the hour.** The `ebooks` library scans at minute 15 of every hour, because the watcher does not see writes made on the NFS server and the `abs-notify` hook only handles audiobooks.
- **Invitees see every library by default.** New ABS accounts get "access all libraries". Untick it and choose libraries for anyone who should not see the ebook shelf.

Create the library once, after the `/ebooks` mount is deployed (the API needs an admin token; never echo it):

```bash
TOKEN="$(op read op://kdk-ops/audiobookshelf-api-root/credential | tr -d '[:space:]')"
curl -s -X POST https://audiobookshelf.kmkdp.com/api/libraries \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"ebooks","mediaType":"book","icon":"book-1","provider":"google",
       "folders":[{"fullPath":"/ebooks"}],
       "settings":{"coverAspectRatio":0,"autoScanCronExpression":"15 * * * *",
         "metadataPrecedence":["folderStructure","audioMetatags","nfoFile","txtFiles","opfFile","absMetadata"]}}'
```

## Reading-order collections

`stacks/audiobookshelf/tools/abs_collections.py` keeps three ABS collections in step with the vault note `4. Learning/Content/Reading List.md`:

| Collection | Library | From the note |
|---|---|---|
| Round 1 | `ebooks` | every row of the table under the `## Round 1: …` heading |
| Reading order | `ebooks` | every row of both tables |
| Audio queue | `audiobooks` | the numbered *Audio queue* list |

Plappa 2.0 shows these collections for ebooks and audiobooks, so the phone follows the list.

- **Matching:** exact ISBN if the row has one, otherwise the normalised title (no subtitle, edition words, punctuation or case), with the author and then the edition as tie-breaks. A title that is not in ABS, is ambiguous, or names another edition is printed and skipped, never guessed. A row whose Status is `missing` or `NEW` is skipped even when ABS has that title, so a stray older edition (e.g. the 1st-edition *Fundamentals of Software Architecture*) stays out; set the row to `ABS <format>` once the right file is in the library. `DOUBT:` marks a title-prefix match to check by eye.
- **Writes:** creates a missing collection, then sets its books and order exactly to the list: books that left the list are removed, new arrivals join in list order. Finished books stay unless they left the list. A second `--apply` makes 0 writes.
- **Run it on demand**, from the laptop, after editing the list or after new books arrive. Dry run is the default; read the match lines before `--apply`. The token is read from the environment and never printed:

```bash
export ABS_TOKEN="$(op read op://kdk-ops/audiobookshelf-api-root/credential)"
uv run stacks/audiobookshelf/tools/abs_collections.py           # dry run: matches + planned writes
uv run stacks/audiobookshelf/tools/abs_collections.py --apply   # write them
unset ABS_TOKEN
```

`--list PATH` points at another copy of the note. Tests: `uvx pytest -q stacks/audiobookshelf/tools/tests` (fake ABS API, also in CI). The API it uses: `GET /libraries/<id>/items`, `GET /collections`, `POST /collections`, `POST /collections/<id>/batch/remove`, `POST /collections/<id>/batch/add`, `PATCH /collections/<id>` with `books` (reorders existing members only, so it runs after the removes and adds). To undo, delete the collection in the ABS UI; the books are untouched.

## Secret Rotation

| Secret (1P item) | Used for | Rotate |
|---|---|---|
| `kdk-ops/audiobookshelf-api-root` | `abs-notify` rescan hook (`/etc/kdk/abs-notify.env` on kdk-dkr-01, `ABS_API_TOKEN`) | create a new API key with an expiry in ABS → `op item edit` → `op read` → host file (root 600) → redeploy `media`, all in one step ([C1](#pre-publish-hand-steps-owner)) |

The OIDC client secret lives in [[identity]].
