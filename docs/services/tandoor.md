---
title: Tandoor
host: kdk-dkr-dmz-01
stack: tandoor
tier: dmz
status: live
storage: yes
secrets: [authelia-app-secrets]
updated: 2026-11-08
tags: [homelab/stack, tier/dmz, status/live]
---

# Tandoor

Shared household recipe manager + weekly meal plan + pantry-aware shopping list on the DMZ node. Login is Tandoor's **own** multi-user auth or Authelia OIDC (client `tandoor`), **not** ForwardAuth — so a family member can have a Tandoor account without an Authelia identity (same rationale as [[audiobookshelf]]).

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]] · [[identity]] · [[backups]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Recipes, meal plan, pantry-aware shopping list |
| Host | [[kdk-dkr-dmz-01]] |
| Endpoint | `https://recipes.kmkdp.com` (DMZ Traefik; router on `websecure,tunnel`, but public only once the owner adds the tunnel public hostname, see Publish to the internet below) |
| Image | `ghcr.io/tandoorrecipes/recipes` (all-in-one: internal nginx :80 → gunicorn) + `postgres:18` (`tandoor-db`), both pinned in `stacks/tandoor/compose.yaml` |
| Deploy | Komodo stack `tandoor` (git-linked) |
| Storage | Postgres `tandoor` in `/opt/kdk-lab/tandoor/postgres`; `staticfiles` + `mediafiles` local on disk |
| Backup | Postgres is `pg_dump`ed nightly by `backup-dmz` (`tandoor-YYYY-MM-DD.sql.gz`, keeps 15, in `/mnt/backup/dkr/kdk-dkr-dmz-01/pgdump/`); the live `tandoor/postgres` cluster dir is excluded from the rsync. `mediafiles` (uploaded images) rides the rsync offsite; `staticfiles` is excluded (regenerable). See [[backups]] |
| Blast radius | No recipes / meal plan / shopping list |

## Households / spaces

Each household gets its own Tandoor **space**: its own recipes, meal plan and shopping list. Members of one space cannot see another space.

| Space (id) | Household | Members |
|---|---|---|
| Mack House (3) | The Macks | jmack, rach (admin), `dashboard` (user, API token for the home dashboard) |
| Ketelaar (5) | Ketelaar | jketelaar (admin, local account, 2026-11-08) |

`admin's Space` (2) and `kdkadmin's Space` (4) are the automatic personal spaces of the two admin accounts.

**Gotcha:** with `SOCIAL_DEFAULT_ACCESS=1`, a person's **first** "Sign in with Authelia" also adds them as a guest to `Space.objects.first()` (today that is `admin's Space`, not Mack House), and Tandoor creates a "<user>'s Space" for them as well. So for a person who belongs only in their own household, create a **local** account in that space instead (one `manage.py shell` run inside `tandoor-web`, using `/opt/recipes/venv/bin/python`). Put the temporary password in 1Password, and the person can link Authelia afterwards from their account settings. If someone does go through the Authelia signup, remove the extra memberships afterwards under Space settings → Members.

Reachability: `recipes.kmkdp.com` is LAN-only until the tunnel public hostname exists, so people outside the house need Tailscale.

**Space quotas for invitee households.** Every space starts unlimited (`max_users=0`, `max_recipes=0`, `max_file_storage_mb=0`). For a space whose admin is not us, set limits so a compromised or careless space admin cannot invite without bound or fill the disk with uploads. Recommended: `max_users` **6** and `max_file_storage_mb` **2048** (Ketelaar; optionally Mack House too). Set them as `kdkadmin` on the LAN (Django admin → Spaces, or space settings); the `TandoorUploadBurst` alert covers the gap until then.

## Hardening

Compose baseline ([compose-baseline](../reference/compose-baseline.md)) since 2026-11-08:

| Item | `tandoor-web` | `tandoor-db` |
|---|---|---|
| `no-new-privileges` | yes | yes |
| `cap_drop: [ALL]` + `cap_add` | `CHOWN, SETUID, SETGID, DAC_OVERRIDE, FOWNER` (image runs as root: nginx master drops to `nginx`, boot.sh `chmod -R` on mediafiles). No `NET_BIND_SERVICE`: Docker sets `ip_unprivileged_port_start=0`, so :80 binds without it | same set, a no-op while `user: "70:70"` (kept for a rollback that drops `user:`) |
| User | root (image design) | `70:70` (data dir already owned by uid 70) |
| `read_only` | **skipped** (`kdk.baseline.skip`): boot.sh writes the rendered nginx config into `/opt/recipes/http.d` at every start | yes, tmpfs `/run/postgresql`, `/tmp` |
| `mem_limit` / `pids_limit` | 2g / 256 (≈1 GiB, 24 pids in use) | 512m / 128 (≈63 MiB, 13 pids) |
| Healthcheck | busybox `wget` of `/accounts/login/` with `Host: recipes.kmkdp.com` (else Django 400s) and an `X-Forwarded-For` header (see the proxy-count gotcha) | `pg_isready` |
| Env | full `.env` (app secrets) | only `POSTGRES_*` (no `env_file`), so `SECRET_KEY` and the OIDC client secret never reach the db |
| Network | `tandoor_default` + `proxy-dmz` (egress) | `tandoor_default` only, `internal: true` |

`tandoor_default` is shared with `backup-dmz`'s `pgdump-tandoor` (joins it as external). Compose recreates the network when `internal` changes, so after the first deploy with this setting redeploy `backup-dmz` too, or the stopped one-shot points at the old network id.

## Publish to the internet

Status: prepared, **not published**. The repo side is done (router on `websecure,tunnel`, `ALLAUTH_TRUSTED_PROXY_COUNT=2`, hardening, Loki rules `TandoorLoginFailures` / `TandoorAdminFromInternet` / `TandoorSpaceOrUserCreated` / `TandoorPasswordLoginFromInternet` / `TandoorUploadBurst` in `stacks/loki/rules/mobile-apps.yml`). Full checklist and evidence: vault `10.Homelab/Security/tandoor-public-readiness-2026-11-08.md`, section 7.

Owner steps, in order, after the compose change is deployed and validated:

1. **Cloudflare custom rule 1** "Block admin APIs" (kmkdp.com → Security → Security rules). Append before the final `)`:

   ```
   or (http.host eq "recipes.kmkdp.com" and starts_with(http.request.uri.path, "/admin"))
   ```

2. **Cloudflare rate-limit rule** "Credential endpoints". Add to the `in {…}` path set:

   ```
   "/_allauth/browser/v1/auth/login" "/_allauth/browser/v1/auth/signup" "/_allauth/browser/v1/auth/password/request" "/_allauth/browser/v1/auth/password/reset" "/_allauth/browser/v1/auth/reauthenticate"
   ```

   and append to the expression:

   ```
   or starts_with(lower(http.request.uri.path), "/invite/")
   ```

   Keep `/_allauth/browser/v1/auth/session` out (the SPA polls it). Do not block `/api/space/`, `/accounts/signup/` or `/_allauth/browser/v1/auth/signup` (the invite flow needs them; signup is closed without an invite token).

3. **Space quotas** for invitee spaces (see Households / spaces above).
4. **Public hostname** (Zero Trust → Networks → Tunnels → Public hostname → Add): `recipes.kmkdp.com` → HTTPS `traefik:8443`, TLS → Origin Server Name `recipes.kmkdp.com` (origin pattern as in [[dmz-proxy]]).
5. Post-publish probes from off-LAN: `/admin/` → 403 (edge), `/_allauth/browser/v1/config` → 200 with `is_open_for_signup: false`, a login burst → 400s then 429s and one `TandoorLoginFailures`.

Rollback: delete the public hostname (edge returns 530 again); the rule edits can stay.

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://recipes.kmkdp.com/            # 200/302
ssh kdkadmin@192.168.191.20 'ls -t /mnt/backup/dkr/kdk-dkr-dmz-01/pgdump/tandoor-*.sql.gz | head'   # dated dumps
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Everyone gets 429 on password login; `usersessions_usersession.ip` is `10.211.2.3` for every row | allauth takes the client IP as `X-Forwarded-For[-N]`; the image's nginx appends Traefik, so the default N=1 is Traefik and the per-IP limits (login 3/min) become one global bucket | keep `ALLAUTH_TRUSTED_PROXY_COUNT: "2"` on `web` |
| 500 on `/_allauth/*` for a direct container-to-container call | with proxy count 2, a request with fewer than two XFF entries raises `ImproperlyConfigured` | go through Traefik (`https://recipes.kmkdp.com`), or send an `X-Forwarded-For` header (the healthcheck does) |
| Healthcheck 400 | Host `127.0.0.1` not in `ALLOWED_HOSTS` | the probe sends `Host: recipes.kmkdp.com` |
| `Operation not permitted` in `docker logs tandoor-web` after a deploy | a capability the image needs is missing from `cap_add` | add the one named by the error (comment why); rollback = delete `cap_drop`/`cap_add` |
| `pgdump-tandoor` fails with `network … not found` | `tandoor_default` was recreated (e.g. `internal` changed) | redeploy `backup-dmz` |
| CSRF error on login/POST | origin not trusted | keep `CSRF_TRUSTED_ORIGINS=https://recipes.kmkdp.com` (Traefik terminates TLS) |
| No "Sign in with Authelia" button | OIDC provider not installed | `SOCIAL_PROVIDERS` must include `allauth.socialaccount.providers.openid_connect` (see `.env.tpl`) |
| Nightly `pg_dump` writes nothing | `/mnt/backup/dkr/kdk-dkr-dmz-01/pgdump` missing on the host | create the dir (see [[backups]]); `pgdump-tandoor` runs over the `tandoor_default` network |

## Configuration

| Path / setting | What it is |
|---|---|
| `/opt/kdk-lab/tandoor/postgres` | PostgreSQL 18 cluster (PGDATA pinned to the 16-era path) (pg_dumped nightly; excluded from rsync) |
| `/opt/kdk-lab/tandoor/mediafiles` | uploaded recipe images (rsynced + offsite) |
| `/opt/kdk-lab/tandoor/staticfiles` | Django static assets (rsync-excluded from offsite; regenerable) |
| `stacks/tandoor/.env.tpl` | op-injected: `SECRET_KEY`, `POSTGRES_PASSWORD`, OIDC client secret; `POSTGRES_DB`/`POSTGRES_USER` are literals (`tandoor`) |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `authelia-app-secrets` (`tandoor_postgres_password`) | DB + `pg_dump` auth | 🟢 self-service | rotate with the DB, then redeploy `tandoor` + `backup-dmz` |
| `authelia-app-secrets` (`tandoor_secret_key`) | Django `SECRET_KEY` | 🟢 self-service | redeploy `tandoor` |
| `authelia-app-secrets` (`tandoor_oidc_plaintext`) | Authelia OIDC client secret | 🟢 self-service | must match the `tandoor` client in [[identity]] |
