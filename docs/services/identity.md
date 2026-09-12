---
title: Identity (Authelia + LLDAP)
host: kdk-dkr-dmz-01
stack: identity
tier: dmz
status: live
storage: yes
secrets: [authelia-app-secrets, authelia-postgres, lldap-admin, lldap-app-secrets, proton-bridge-smtp-incluster]
updated: 2026-09-12
tags: [homelab/stack, tier/dmz, status/live]
---

# Identity — Authelia + LLDAP

Single sign-on for every `*.kmkdp.com` service; lives on the DMZ node so authentication needs no DMZ→LAN holes.

**Related:** [[kdk-dkr-dmz-01]] · [[dmz-proxy]] · [[traefik]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Authelia ForwardAuth + OIDC, LLDAP directory |
| Status | 🟢 Live — `sso.kmkdp.com` 200; login + TOTP works |
| Host | [[kdk-dkr-dmz-01]] (192.168.191.20) |
| Endpoints | `https://sso.kmkdp.com` (Authelia), `https://users.kmkdp.com` (LLDAP) |
| Images | authelia `4.39.20`, lldap `v0.6.3-alpine`, postgres `16-alpine`, redis `8-alpine` |
| Deploy | Komodo stack `identity` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/identity/{lldap,postgres,authelia-secrets}` (VM disk) |
| Blast radius | Every gated UI 500s; no logins lab-wide |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://sso.kmkdp.com/     # 200
docker exec authelia-postgres pg_isready -U authelia               # accepting connections
```
Then log in at `sso.kmkdp.com` with an existing user + TOTP.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| All logins fail after a restore | storage key / DB mismatch | key and DB are PAIRED — restore both from the same point |
| TOTP no longer accepted | storage encryption key changed | never rotate `storage_encryption_key` alone |
| ForwardAuth 500 from other services | Authelia down or Redis unreachable | `docker ps` — authelia, redis Up |
| LDAP bind fails | `lldap-admin` password drift | Authelia and LLDAP must share `LLDAP_ADMIN_PASSWORD` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[dmz-proxy]] | ingress + TLS on the DMZ |
| Needs | proton-bridge (apps) | password-reset / notifier email |
| Needed by | [[traefik]] ForwardAuth + every gated UI | no authentication anywhere |

## Observability

No dashboard. `sso.kmkdp.com` probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

`authelia:9091` and `lldap:17170` join `proxy-dmz` for ingress; `postgres:5432`, `redis:6379` stay internal. LLDAP serves **LDAPS on 6360** (self-signed cert for `lldap`, `/opt/kdk-lab/identity/lldap-certs/`) — Jellyfin binds over LDAPS — plus plain `3890` which Authelia uses (`ldap://lldap:3890`). Config is templated from `configuration.yml` + `configuration.webauthn.yml`.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/identity/authelia/configuration.yml` | Access rules, session, OIDC clients (templated) |
| `stacks/identity/authelia/configuration.webauthn.yml` | WebAuthn settings |
| `/opt/kdk-lab/identity/authelia-secrets/` | secret files mounted read-only |
| `LLDAP_LDAP_BASE_DN` | `dc=kmkdp,dc=com` |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `authelia-app-secrets/storage_encryption_key` | encrypts TOTP/WebAuthn at rest | 🔴 never on live | paired with the Postgres DB — replacing it invalidates all 2FA |
| `authelia-app-secrets/session_encryption_key` | session cookies | 🟢 self-service | yes — logs everyone out |
| `authelia-app-secrets/oidc_hmac_secret` | OIDC signing | 🟡 breaks clients | rotate + reconfigure every OIDC client |
| `authelia-postgres` | DB auth | 🟢 self-service | rotate DB + item together |
| `lldap-admin` | directory admin bind | 🟢 self-service | must match Authelia's LDAP password |
| `lldap-app-secrets` (`jwt_secret`, `key_seed`) | LLDAP tokens/derivation | 🔴 `key_seed` never on live | changing `key_seed` invalidates stored secrets |
| `proton-bridge-smtp-incluster` | notifier SMTP | 🟡 external party | rotate in Proton + 1P |

## User Guide

Users manage their profile and TOTP at `sso.kmkdp.com`; admins manage accounts and groups at `users.kmkdp.com`.
