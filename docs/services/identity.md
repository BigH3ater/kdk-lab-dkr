---
title: Identity (Authelia + LLDAP)
---
# Identity — Authelia + LLDAP

SSO for every `*.kmkdp.com` service. Lives on the DMZ node so auth needs no
DMZ→LAN holes.

| | |
|---|---|
| Host | kdk-dkr-dmz-01 (192.168.191.20) |
| URLs | https://sso.kmkdp.com (Authelia) · https://users.kmkdp.com (LLDAP UI) |
| Stack | `stacks/identity` — authelia 4.39.20, lldap 0.6.3, postgres 16, redis 8 |
| Data | /opt/kdk-lab/identity (VM disk); secret files under authelia-secrets/ |
| Secrets | authelia-app-secrets, authelia-postgres, authelia-smtp, lldap-admin, lldap-app-secrets (kdk-cluster) |
| Migrated | 2026-09-11 from k3s: lldap /data copy + authelia pg_dump; same storage key, TOTP enrollments intact |

The storage encryption key and the postgres database are PAIRED — never
replace one without the other.

Verify: `curl -s https://sso.kmkdp.com` → 200; login with an existing user + TOTP.
