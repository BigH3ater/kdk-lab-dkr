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
| Endpoint | `https://recipes.kmkdp.com` (DMZ Traefik, `websecure` entrypoint) |
| Image | `ghcr.io/tandoorrecipes/recipes` (all-in-one: internal nginx :80 → gunicorn) + `postgres:16` (`tandoor-db`), both pinned in `stacks/tandoor/compose.yaml` |
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

Reachability: `recipes.kmkdp.com` is LAN-only (no tunnel hostname), so people outside the house need Tailscale.

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://recipes.kmkdp.com/            # 200/302
ssh kdkadmin@192.168.191.20 'ls -t /mnt/backup/dkr/kdk-dkr-dmz-01/pgdump/tandoor-*.sql.gz | head'   # dated dumps
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| CSRF error on login/POST | origin not trusted | keep `CSRF_TRUSTED_ORIGINS=https://recipes.kmkdp.com` (Traefik terminates TLS) |
| No "Sign in with Authelia" button | OIDC provider not installed | `SOCIAL_PROVIDERS` must include `allauth.socialaccount.providers.openid_connect` (see `.env.tpl`) |
| Nightly `pg_dump` writes nothing | `/mnt/backup/dkr/kdk-dkr-dmz-01/pgdump` missing on the host | create the dir (see [[backups]]); `pgdump-tandoor` runs over the `tandoor_default` network |

## Configuration

| Path / setting | What it is |
|---|---|
| `/opt/kdk-lab/tandoor/postgres` | PostgreSQL 16 cluster (pg_dumped nightly; excluded from rsync) |
| `/opt/kdk-lab/tandoor/mediafiles` | uploaded recipe images (rsynced + offsite) |
| `/opt/kdk-lab/tandoor/staticfiles` | Django static assets (rsync-excluded from offsite; regenerable) |
| `stacks/tandoor/.env.tpl` | op-injected: `SECRET_KEY`, `POSTGRES_PASSWORD`, OIDC client secret; `POSTGRES_DB`/`POSTGRES_USER` are literals (`tandoor`) |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `authelia-app-secrets` (`tandoor_postgres_password`) | DB + `pg_dump` auth | 🟢 self-service | rotate with the DB, then redeploy `tandoor` + `backup-dmz` |
| `authelia-app-secrets` (`tandoor_secret_key`) | Django `SECRET_KEY` | 🟢 self-service | redeploy `tandoor` |
| `authelia-app-secrets` (`tandoor_oidc_plaintext`) | Authelia OIDC client secret | 🟢 self-service | must match the `tandoor` client in [[identity]] |
