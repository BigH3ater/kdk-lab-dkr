---
title: Wizarr
host: kdk-dkr-dmz-01
stack: wizarr
tier: dmz
status: live
storage: yes
secrets: [wizarr-ldap, lldap-admin, jellyfin]
updated: 2026-10-27
tags: [homelab/stack, tier/dmz, status/live]
---

# Wizarr (wizarr.kmkdp.com)

Jellyfin invite manager: issues `https://wizarr.kmkdp.com/j/<CODE>` links, and on an
accepted invite creates an **lldap** account (`uid=<name>,ou=people,dc=kmkdp,dc=com`)
plus a **Jellyfin** account, then runs the invitee through the onboarding wizard.
Internet-facing via the Cloudflare tunnel, with **path-scoped public exposure**: only
the invitee paths are reachable without auth; the admin portal is behind Authelia.

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]] · [[identity]] · [[jellyfin]] · [[seerr]] · [[media]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Jellyfin invites + onboarding; creates lldap + Jellyfin accounts |
| Status | 🟢 Live — invitee paths open; admin 302s to SSO |
| Host | [[kdk-dkr-dmz-01]] (192.168.191.20) |
| Endpoint | `https://wizarr.kmkdp.com` (public invitee paths; admin behind Authelia) |
| Image | `ghcr.io/wizarrrr/wizarr:v2026.10.1` (lldap create needs ≥ 2026.10.0: sends cn/sn, lowercases uids) |
| Deploy | Komodo stack `wizarr` (git-linked) on kdk-dkr-dmz-01 |
| Storage | `/mnt/appdata/wizarr` → `/data/database` (sqlite `database.db` + `secrets.json` + `sessions/`, ~6.6 MB) |
| Blast radius | No new invites / onboarding; existing Jellyfin/Seerr users unaffected |

## Public-path allowlist (why these paths are open)

The DMZ Traefik splits `wizarr.kmkdp.com` into two routers sharing one service
(port 5690). The **public** router matches `Host` **and** the invitee-path allowlist
below (no auth, priority 100); the **admin** router matches `Host` only (ForwardAuth →
Authelia, priority 1) and so catches everything else. Higher priority wins, so an
allowlisted path skips auth and every other path is forced through `sso.kmkdp.com`.

Allowlist (Traefik `PathRegexp`, matches the **path only** — query strings are
irrelevant to Traefik, so the Authelia-doc `([/?].*)?` forms are reduced to `(/.*)?`):

| Regex | Covers |
|---|---|
| `^/j(/.*)?$` | invite links `/j/<CODE>` (and `/j/<CODE>/password`) |
| `^/join(/.*)?$` | enter-invite-code page + POST join |
| `^/invitation/process(/.*)?$` | invitation processing POST |
| `^/plex/callback(/.*)?$` | Plex OAuth callback (harmless; this is a Jellyfin server) |
| `^/wizard(/.*)?$` | onboarding wizard |
| `^/reset/.+$` | password-reset links (`/reset/<code>`) |
| `^/static(/.*)?$` | JS/CSS/PWA manifest + icons |
| `^/image-proxy(/.*)?$` | poster/image proxy |
| `^/cinema-posters(/.*)?$` | onboarding poster wall |
| `^/favicon\.ico$` | favicon |

Source: Wizarr's SSO docs (`docs.wizarr.dev/using-wizarr/single-sign-on-sso`),
cross-checked against the public blueprint (`wizarrrr/wizarr`,
`app/blueprints/public/routes.py`). **Never** allowlisted:

- **`/login`** — with `DISABLE_BUILTIN_AUTH=true` it would sign every visitor in as
  admin. We run `DISABLE_BUILTIN_AUTH=false` (Wizarr's own login stays as a second
  layer) **and** keep `/login` behind Authelia.
- **`/api`** — invitations don't need it; keeping it private avoids exposing the API docs.
- **`/setup`** — first-run setup is treated as **admin-only** here (behind Authelia),
  not public.

In the compose file every regex anchor `$` is written `$$` because docker compose
interpolates a lone `$`; `docker compose config` shows the single `$` that Traefik
receives.

## Verify

```bash
curl -sI https://wizarr.kmkdp.com/j/TEST      # 200/302 from Wizarr (invite path, no SSO)
curl -sI https://wizarr.kmkdp.com/admin       # 302 -> sso.kmkdp.com (Authelia)
curl -sI https://wizarr.kmkdp.com/login       # 302 -> sso.kmkdp.com
curl -sI 'https://wizarr.kmkdp.com/api/users' # 302 -> sso.kmkdp.com
```
Komodo shows `wizarr` **Deployed / running** on kdk-dkr-dmz-01; `wizarr.kmkdp.com`
resolves to `192.168.191.20` (derived by `dns-reconcile` from the Host() label + the
stack's server — moving the stack moved the record).

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| Invite link 302s to SSO | path not in the public allowlist (or admin router won priority) | `docker inspect wizarr` labels — public router priority `100`, rule has the `PathRegexp` for `/j` |
| Admin portal reachable without login | public allowlist too broad / forwardauth missing | admin router has `middlewares=wizarr-auth` → `http://authelia:9091/api/authz/forward-auth` |
| Invited user cannot log in via LDAP/SSO | invite not yet reconciled (no jellyfin-users group / default Jellyfin provider / not in Seerr) | wait for the next `:07/:22/:37/:52` `wizarr-sync` run, or run the Procedure now |
| lldap create fails on invite | Wizarr < 2026.10.0 (no cn/sn, mixed-case uid) | image is `v2026.10.1`; check Wizarr logs for the lldap error |
| 530/502 from the tunnel | CF public hostname missing/origin-server-name wrong | add `wizarr.kmkdp.com` → `https://traefik:8443` (the `tunnel` entrypoint; only the `wizarr-public` router listens there, admin paths 404 off-LAN), TLS origin server name = `wizarr.kmkdp.com` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[dmz-proxy]] + Cloudflare tunnel | ingress + public access |
| Needs | [[identity]] (Authelia + lldap) | admin ForwardAuth; invite-created directory accounts |
| Needs | [[jellyfin]] | the media server invites grant access to |
| Needed by | [[jellyfin]], [[seerr]] | new household users (via `wizarr-sync`) |

## Observability

No dashboard. `wizarr.kmkdp.com` probed by [[kdk-mon-01]]; the `wizarr-sync` run pages
Pushover on error (and on any change it made).

## Architecture

Single container on `proxy-dmz` (port 5690) and the internal `identity_ldap` network, where
`lldap.kmkdp.com` resolves to lldap for LDAPS :6360 (since 2026-10-28; lldap is no longer on
`proxy-dmz`, and [[identity]] creates the network, so deploy identity first). The DMZ Traefik host-routes two routers to
it: `wizarr-public` (invitee allowlist, no auth, priority 100) and `wizarr-admin`
(ForwardAuth → `authelia:9091`, priority 1). `DISABLE_BUILTIN_AUTH=false` keeps Wizarr's
own admin login as a second layer behind Authelia. Data on `/mnt/appdata/wizarr`.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/wizarr/compose.yaml` | image, volume, both Traefik routers + `wizarr-auth` middleware |
| `DISABLE_BUILTIN_AUTH=false` | keep Wizarr's own login (second layer) |
| Wizarr → Settings → LDAP | stored in Wizarr's DB (not git); see the table below |
| `stacks/wizarr-sync/` | the invite-reconcile one-shot (below) |

### Wizarr → LLDAP (invites create directory accounts)

Wizarr creates the **lldap user** on an accepted invite, then the Jellyfin account.
Settings → LDAP (stored in Wizarr's own DB, not in git):

| Field | Value |
|---|---|
| Server URL | `ldaps://lldap.kmkdp.com:6360` (Use TLS on, Verify certificate **on**: LE wildcard; name via the `identity_ldap` network alias) |
| Base DN | `dc=kmkdp,dc=com` |
| Service account DN | `uid=wizarr,ou=people,dc=kmkdp,dc=com` (member of `lldap_password_manager`, password `op://kdk-cluster/wizarr-ldap/password`) |
| User base DN / filter / attrs | `ou=people,dc=kmkdp,dc=com` · `(uid={username})` · `uid` · `mail` · object class `person` |
| Group base DN / class / member attr | `ou=groups,dc=kmkdp,dc=com` · `groupOfUniqueNames` · `uniqueMember` |
| Admin LDAP bind | off (Wizarr admin stays local) |

**Group caveat:** Wizarr never adds the new user to a group, so the Jellyfin LDAP plugin
(members of `jellyfin-users` only) and the group-based Authelia rules reject them until
they are added. This is now reconciled automatically by `wizarr-sync` (below) — no manual
`users.kmkdp.com` step.

## Automation: wizarr-sync

`stacks/wizarr-sync/` is an idempotent one-shot (python:3.12-alpine, stdlib only) run by
the `wizarr-sync` Komodo Procedure every 15 min (`:07,:22,:37,:52` America/Chicago) on
kdk-dkr-dmz-01. For every Wizarr `user` row with `is_ldap_user=1` it:

1. **lldap** — ensures membership in `jellyfin-users` (via GraphQL `addUserToGroup`), and
   nothing else. (lldap admin password `op://kdk-cluster/lldap-admin/password`.)
2. **Jellyfin** — if the account's `AuthenticationProviderId` is not the LDAP plugin
   (`Jellyfin.Plugin.LDAP_Auth.LdapAuthenticationProviderPlugin`), sets it with a full
   `POST /Users/{id}/Policy` round-trip. (API key `op://kdk-cluster/jellyfin/api-key`.)
3. **Seerr** — if no Seerr user has that Jellyfin user id, calls
   `POST /api/v1/user/import-from-jellyfin` `{"jellyfinUserIds":[id]}`. (Seerr API key read
   read-only from `/mnt/appdata/jellyseerr/settings.json` `main.apiKey`.)

No other groups, no permission changes. The Wizarr sqlite is mounted read-only; it is
WAL mode, so the one-shot copies `database.db` + `-wal` + `-shm` into a writable tmp dir
and opens the copy (a `?mode=ro` connection fails on a read-only WAL directory). It
reaches `lldap:17170` on `identity_default` and
`jellyfin:8096` / `seerr:5055` on `proxy-dmz`. One log line per action; `DRY_RUN=1`
reports without changing anything; Pushover summary only when something changed or
errored (creds `/etc/kdk/pushover.env`); exits 1 on errors.


> [!warning] Which users the sync touches
> Only users that came through a Wizarr **invitation** (`user` JOIN `invitation_user`) with
> `is_ldap_user=1`, minus `EXCLUDE_USERS` (`admin,wizarr,jellyfin-bind`). Wizarr's own
> "Sync all LDAP users" button imports *every* directory account into its `user` table with
> the LDAP flag set and no invitation link; the first run (2026-10-27) selected those and
> refused them at the Jellyfin step. The join is what keeps service accounts out.

**Server URL on the DMZ:** kdk-dkr-dmz-01 resolves with public DNS (1.1.1.1/9.9.9.9), so
`https://jellyfin.kmkdp.com` only works there while Jellyfin is published on the tunnel.
Wizarr's media server is therefore `http://jellyfin:8096` (container name on `proxy-dmz`)
and the wizard's **External URL** is `https://jellyfin.kmkdp.com` for what invitees see.

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `wizarr-ldap` (kdk-cluster) | Wizarr's lldap service account (invite creates) | 🟢 self-service | rotate in lldap + Wizarr Settings → LDAP |
| `lldap-admin` (kdk-cluster) | `wizarr-sync` GraphQL (add to jellyfin-users) | 🔴 identity core | rotate carefully; shared with Authelia/lldap |
| `jellyfin` → `api-key` (kdk-cluster) | `wizarr-sync` sets the Jellyfin LDAP provider | 🟢 self-service | regenerate in Jellyfin → Dashboard → API Keys, update 1P |

## User Guide

Admin: open `wizarr.kmkdp.com` (Authelia + Wizarr login), create an invite, send the
`/j/<CODE>` link. The invitee opens the link, sets a username/password, and is onboarded
to Jellyfin; within ~15 min `wizarr-sync` grants them LDAP/SSO login and a Seerr account.
Once synced, invitees sign in to the Jellyfin web UI either with **Sign in with SSO** (Authelia, `jellyfin-users`) or with the username/password they chose (LDAP); TVs and phone apps use Quick Connect or that password. See [[jellyfin]] § SSO.
