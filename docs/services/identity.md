---
title: Identity (Authelia + LLDAP)
host: kdk-dkr-dmz-01
stack: identity
tier: dmz
status: live
storage: yes
secrets: [authelia-app-secrets, authelia-postgres, authelia-redis, lldap-admin, lldap-authelia-bind, lldap-app-secrets, proton-bridge-smtp]
updated: 2026-10-28
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
| Endpoints | `https://sso.kmkdp.com` (Authelia, public), `https://users.kmkdp.com` (LLDAP UI, LAN/tailnet, behind Authelia `group:lldap_admin`), LDAPS `lldap.kmkdp.com:6360` |
| Images | authelia `4.39.28`, lldap `v0.6.3-alpine`, postgres `16.15-alpine`, redis `8.10.3-alpine`, traefik-certs-dumper `v2.11.4` (pinned 2026-10-28; Renovate proposes updates) |
| Deploy | Komodo stack `identity` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/identity/{lldap,postgres,authelia-secrets}` (VM disk; `authelia-secrets` owned `8091:8091`), Redis volume `identity_redis-data` |
| Blast radius | Every gated UI 500s; no logins lab-wide |

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://sso.kmkdp.com/     # 200
curl -s https://sso.kmkdp.com/api/health                           # {"status":"OK"}
docker exec authelia-postgres pg_isready -U authelia               # accepting connections
docker top authelia -eo uid,comm                                   # 8091 authelia (never 0)
curl -s -o /dev/null -w '%{http_code}\n' https://users.kmkdp.com/   # 302 -> sso.kmkdp.com
curl -s https://radarr.kmkdp.com/ping                              # {"status":"OK"} without a session
docker exec jellyfin nc -zvw2 lldap.kmkdp.com 6360                 # open (identity_ldap)
docker exec seerr nc -zvw2 lldap 3890                              # fails: lldap is not on proxy-dmz
```
Then log in at `sso.kmkdp.com` with an existing user + TOTP.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| All logins fail after a restore | storage key / DB mismatch | key and DB are PAIRED — restore both from the same point |
| TOTP no longer accepted | storage encryption key changed | never rotate `storage_encryption_key` alone |
| ForwardAuth 500 from other services | Authelia down or Redis unreachable | `docker ps` — authelia, redis Up |
| Authelia ignores a merged config change (e.g. new access rule still 403) | Authelia only reads config at start. A config-only merge now recreates authelia on its own via the `kdk.config-sha` label (pre_deploy hashes `configuration.yml` + `configuration.webauthn.yml` into `AUTHELIA_CONFIG_SHA`); only a host-side edit that never reaches git still needs a hand restart | `docker inspect authelia --format '{{index .Config.Labels "kdk.config-sha"}}'` should match the files; otherwise `RestartStack identity services=[authelia]` |
| Need to roll back from 4.39.28 to 4.39.20 | 4.39.21–.28 add storage schema migrations V0025–V0029, all with down scripts (run automatically on first start) | with the 4.39.28 image: `authelia storage migrate down --target 24` (needs the same config/encryption key), then change the tag; or restore the pre-upgrade `pg_dump` |
| LDAP bind fails (`Invalid Credentials` for `authelia-bind`) | `lldap-authelia-bind` 1Password item and the lldap user's password differ, or the user is missing | reset `authelia-bind` in `users.kmkdp.com` to the 1P value, redeploy; it must be in `lldap_password_manager` |
| Password reset/change at sso fails for one user | the user is in `lldap_admin`; the `authelia-bind` password manager cannot change an admin's password (by design since 2026-10-28) | change it in `users.kmkdp.com` instead |
| Authelia restart-loops after the 2026-10-28 hardening | `permission denied` on `/secrets/...` (host tree not `chown 8091`), or the MDS3 download failed (`webauthn.metadata`, strict) | `docker logs authelia --since 5m`; `sudo chown -R 8091:8091 /opt/kdk-lab/identity/authelia-secrets`; for MDS check egress to `mds3.fidoalliance.org` or set `metadata.enabled: false` |
| Every login 500s, log says `NOAUTH` / redis auth | `AUTHELIA_REDIS_PASSWORD` missing from `.env` or not alphanumeric | `docker exec authelia-redis redis-cli ping` (uses `REDISCLI_AUTH`) → `PONG` |
| OIDC login refused with `access_denied` for one app | the user is not in the client's group (`ha-users`, `audiobookshelf-users`, `immich-users`, `internal-users` for Libreseerr) | add the group in `users.kmkdp.com` |
| `users.kmkdp.com` 403 after SSO | the SSO user is not in `lldap_admin` | log in as a directory admin (break-glass `admin`, or add your account to `lldap_admin`) |
| Jellyfin/Wizarr LDAP: `no such host lldap.kmkdp.com` | the container is not on `identity_ldap` (e.g. identity redeployed with `down`, network recreated) | `docker network inspect identity_ldap`; redeploy jellyfin / wizarr |
| Authelia: `x509: certificate` / `tls: failed to verify` on LDAP | lldap is serving a stale or wrong cert (dumper not run, or restart missed after renewal) | `openssl s_client -connect 192.168.191.20:6360 -servername lldap.kmkdp.com` and check `notAfter`; `docker logs lldap-certs-dumper`; run procedure `identity-ldaps-refresh` |
| lldap restart-loops with `No such file` for `/certs/kmkdp.com/...` | First deploy raced the dumper, or acme.json has no `kmkdp.com` cert yet | Wait for the dumper (seconds); confirm the DMZ Traefik holds the wildcard |
| LAN client (kdk-mon-01 probe, workstation) cannot reach LDAPS | VLAN 20 → DMZ port 6360 blocked, or DNS record missing | `nc -zv 192.168.191.20 6360`; `getent hosts lldap.kmkdp.com` → 192.168.191.20 |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[dmz-proxy]] | ingress + TLS on the DMZ (it joins `identity_default` for the lldap UI) |
| Needs | internet egress from Authelia | startup fails: NTP check + FIDO MDS3 download (`webauthn.metadata`, strict) |
| Needs | proton-bridge (apps) | password-reset / notifier email |
| Needed by | [[traefik]] ForwardAuth + every gated UI | no authentication anywhere |
| Needed by | [[jellyfin]], [[wizarr]] (LDAPS via `identity_ldap`) | Jellyfin LDAP logins, Wizarr invite account creation |

## Observability

No dashboard. `sso.kmkdp.com` probed by [[kdk-mon-01]]; alerts → Pushover.

## Architecture

Three networks (2026-10-28, security plan A1.2):

| Network | Members | Why |
|---|---|---|
| `identity_default` | all five services, the DMZ Traefik, `wizarr-sync`, `backup-dmz` | Postgres `:5432` and Redis `:6379` live only here; the DMZ Traefik reaches the LLDAP UI `:17170` here (`traefik.docker.network=identity_default` on lldap); `wizarr-sync` uses `lldap:17170` GraphQL; `backup-dmz` dumps Postgres |
| `identity_ldap` (`internal: true`, created by this stack) | lldap (alias `lldap.kmkdp.com`), Authelia, [[jellyfin]], [[wizarr]] | the LDAP clients reach LDAPS `:6360` under the certificate's name; no gateway, no egress |
| `proxy-dmz` | Authelia only | `sso.kmkdp.com` router and the ForwardAuth target `http://authelia:9091` for `wizarr-auth` and `lldap-auth` |

lldap left `proxy-dmz`, so Seerr, Immich, Tandoor, Audiobookshelf, Libreseerr and the other public apps can no longer open `lldap:3890` (plain LDAP), `:6360` or `:17170` (GraphQL) on the bridge (docker-hosts audit H-08). Jellyfin and Wizarr still can, on `identity_ldap`: they already hold bind credentials, and lldap has no switch to turn the plain listener off. Postgres and Redis stay on `identity_default` only. The containers from other stacks on that network are the DMZ Traefik, `wizarr-sync` and `backup-dmz`, and both datastores require a password.

LLDAP serves **LDAPS on 6360** with the lab's **Let's Encrypt wildcard** (since 2026-10-27): the `certs-dumper` sidecar keeps `/opt/kdk-lab/identity/lldap-certs/kmkdp.com/` in sync with the DMZ Traefik's `acme.json`, and the `identity-ldaps-refresh` procedure restarts the `lldap` service on the 1st and 15th because lldap does not reload certificate files. Clients address it as `lldap.kmkdp.com` and verify against public roots: Authelia (`ldaps://lldap.kmkdp.com:6360`, `skip_verify: false`), the Jellyfin LDAP plugin (host `lldap.kmkdp.com`, SkipSslVerify off) and Wizarr (verify on), all through the `identity_ldap` alias. `192.168.191.20:6360` stays published for kdk-mon-01's LDAPS certificate probe and LAN clients (AdGuard A-record via dns-reconcile `EXTRA`). Plain `:3890` is not published.

**Containment (M1).** Authelia runs as `8091:8091` with a read-only rootfs (`/tmp` tmpfs), `cap_drop: [ALL]`, `no-new-privileges`; `server.disable_healthcheck: true` stops it writing `/app/.healthcheck.env` and compose probes `/api/health` itself. Redis runs as `999`, read-only, with `requirepass` (written to a 0600 tmpfs config, never on the command line) and a named `redis-data` volume. Postgres runs as `70` with `/run/postgresql` on tmpfs. lldap (root entrypoint → uid 1000) and the certs-dumper (root, chowns the key to 1000) keep only `CHOWN/SETUID/SETGID/DAC_OVERRIDE/FOWNER` resp. `CHOWN/FOWNER/DAC_OVERRIDE`; those lists are inferred from the entrypoints, so if either container logs `Operation not permitted`, remove its `cap_drop`/`cap_add` lines and redeploy. All five log json-file 10m × 3.

**Authentication policy (2026-10-28).**

- **LDAP bind:** Authelia binds as `uid=authelia-bind` (member of `lldap_password_manager` only), not `admin`. `admin` is **break-glass**: lldap's own admin and the emergency SSO identity; do not use it for day-to-day logins.
- **LLDAP UI** `users.kmkdp.com`: Traefik `lldap-auth` ForwardAuth → Authelia rule `two_factor`, `group:lldap_admin`; LLDAP's login is the second gate. The compose file sets no LLDAP SMTP or `http_url`. If the host `lldap_config.toml` configures LLDAP's reset mail, its link opens on `users.kmkdp.com` and now needs an SSO session first. User self-service resets go through `sso.kmkdp.com`, which this gate does not touch. If the SSO side is down, the LLDAP UI is down too. Break-glass is then lldap's `force_ldap_user_pass_reset` option, set on one restart.
- **Regulation:** `modes: [user, ip]`, 3 failures in 2 min → 5 min ban. IP bans rely on the DMZ Traefik trusting `X-Forwarded-For` only from cloudflared and the internal Traefik. Lift a ban: `docker exec authelia authelia storage bans ip revoke <ip>` (or `... bans user revoke <user>`).
- **Password policy:** `standard`, min 14 characters (Authelia allows one policy; zxcvbn only shows a meter). It applies to Authelia's reset/change pages only, not to passwords set in the LLDAP UI or by Wizarr.
- **Elevated session:** adding/removing TOTP or WebAuthn needs the second factor (`require_second_factor: true`).
- **WebAuthn:** FIDO MDS3 metadata validation on (`cache_policy: strict` → Authelia needs egress at start). A passkey rejected by MDS falls back to TOTP.
- **Bypass rules:** `sso.kmkdp.com`; exactly `^/ping$` on sonarr/radarr/prowlarr (blackbox probes, anonymous `{"status":"OK"}`). Bazarr, SABnzbd and Tdarr are still probed on `/` (no confirmed anonymous health path).
- **Named network** `definitions.network.internal` = VLANs 1/3/20/30, DMZ, tailnet `100.64.0.0/10`, WireGuard break-glass `192.168.2.0/24`. No rule uses it yet.

**OIDC clients and who may use them.**

| Client | Policy | Group gate | PKCE |
|---|---|---|---|
| `homeassistant` | `ha_access` | `ha-users` | S256 |
| `audiobookshelf` | `audiobookshelf_access` | `audiobookshelf-users` | S256 |
| `immich` | `immich_access` | `immich-users` | off (owner decision; secret stored as pbkdf2 digest) |
| `libreseerr` | `libreseerr_access` | `internal-users` | off (Authlib in v0.9.0 not confirmed to send a challenge) |
| `jellyfin` | `jellyfin_access` | `jellyfin-users` or `lldap_admin` | S256 (the plugin's Duende OidcClient always sends it); secret stored as pbkdf2 digest; see [[jellyfin-sso]] |
| `tandoor` | `two_factor` | none | S256 (enforced) |
| `grafana` | `two_factor` | none | off (secret stored as pbkdf2 digest) |
| `tailscale`, `cloudflare-access`, `vikunja`, `actual` | `two_factor` | none | off |

The `netbox` client and the dead `learn-k3s`, `swagger`, `grafana-embed` rules were removed (no stack serves them). Every client secret file under `/secrets/authelia-app-secrets/` now holds a `$pbkdf2-sha512$` digest; the apps keep the plaintext.

**Wizarr's directory account (blast radius).** Wizarr's LDAP service account `uid=wizarr` and Authelia's `uid=authelia-bind` are both in `lldap_password_manager`. That group can create users and **reset the password of every directory user who is not in `lldap_admin`**: household SSO identities and service binds such as `jellyfin-bind`. A Wizarr compromise, a leak of its data directory, or the invite flow's existing-account overwrite (wizarr audit F1) can therefore take over any non-admin SSO identity. The owner keeps Wizarr's LDAP creation on. Mitigations: invite links go to trusted recipients only, with 24 h expiry. Members of `lldap_admin` cannot be reset by either account. The tripwire is the Loki alert `WizarrLdapExistingUserTouched` from the wizarr audit (§7, D3): it matches the log lines `already exists, updating attributes` / `Updated LDAP user password` at severity critical → Pushover. It is **not in the repo yet** and lands with the Loki ruler work. When it fires, check the named user's password and 2FA devices in `users.kmkdp.com`.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/identity/authelia/configuration.yml` | Access rules, session, OIDC clients (templated) |
| `stacks/identity/authelia/configuration.webauthn.yml` | WebAuthn settings |
| `/opt/kdk-lab/identity/authelia-secrets/` | secret files mounted read-only |
| `LLDAP_LDAP_BASE_DN` | `dc=kmkdp,dc=com` |
| `LLDAP_VERBOSE=true` | lldap logs every bind attempt incl. failures (Loki `{container="lldap"}`) |
| `stacks/identity/.env.tpl` | `LLDAP_AUTHELIA_BIND_PASSWORD`, `AUTHELIA_REDIS_PASSWORD` (+ the existing secrets) rendered by `op inject` |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| `authelia-app-secrets/storage_encryption_key` | encrypts TOTP/WebAuthn at rest | 🔴 never on live | paired with the Postgres DB — replacing it invalidates all 2FA |
| `authelia-app-secrets/session_encryption_key` | session cookies | 🟢 self-service | yes — logs everyone out |
| `authelia-app-secrets/oidc_hmac_secret` | OIDC signing | 🟡 breaks clients | rotate + reconfigure every OIDC client |
| `jellyfin-oidc` → `authelia-app-secrets/jellyfin_client_secret` (digest) | Jellyfin SSO plugin client secret | 🟡 breaks Jellyfin SSO until both sides match | new value in 1P, re-hash into the file, `RestartStack identity services=[authelia]`, paste the plaintext into the plugin; see [[jellyfin-sso]] |
| `authelia-postgres` | DB auth | 🟢 self-service | rotate DB + item together |
| `lldap-admin` | lldap admin (break-glass), `wizarr-sync` GraphQL | 🟢 self-service | rotate in lldap + 1P; redeploy identity and run `wizarr-sync` once |
| `lldap-authelia-bind` | Authelia's LDAP bind `uid=authelia-bind` | 🟢 self-service | set the new password on the lldap user, update 1P, redeploy identity |
| `authelia-redis` | Redis `requirepass` (alphanumeric) | 🟢 self-service | update 1P, redeploy identity (Redis and Authelia recreate together; sessions survive) |
| `lldap-app-secrets` (`jwt_secret`, `key_seed`) | LLDAP tokens/derivation | 🔴 `key_seed` never on live | changing `key_seed` invalidates stored secrets |
| `proton-bridge-smtp` (kdk-cluster) | notifier SMTP: the in-lab Proton Mail Bridge on 10.1.20.20:25 (`stacks/apps`), username = the Proton account, sender `no-reply@kmkdp.com` | 🟡 bridge-generated | re-login the bridge (`info` prints the new password) → 1P → redeploy identity |

## User Guide

Users manage their profile and TOTP at `sso.kmkdp.com`; admins manage accounts and groups at `users.kmkdp.com` (SSO as a `lldap_admin` member, then the LLDAP login).

Group memberships that grant app access: `wizarr-admin` (Wizarr admin portal), `internal-users` (arr apps, Komodo, Wizarr admin, Libreseerr), `dashboard-users` (home), `ha-users` (Home Assistant; `ha-admins` = HA admin role), `audiobookshelf-users`, `immich-users`, `jellyfin-users` (Jellyfin LDAP and Jellyfin SSO; Wizarr invitees via `wizarr-sync`).

## Hand steps for the 2026-10-28 auth-boundary change

> [!danger] Before merge, or logins fail
> Create the LLDAP groups **`ha-users`** (jmack, rach), **`ha-admins`** (jmack) and **`audiobookshelf-users`** (every ABS SSO user). Without them Home Assistant and Audiobookshelf SSO are refused by Authelia.

Before merge (in this order):

1. Groups above, in `users.kmkdp.com`. Also confirm the account you administer LLDAP with is in `lldap_admin` (otherwise only `admin` passes the new SSO gate on `users.kmkdp.com`).
2. LLDAP user `authelia-bind` (any e-mail, e.g. `authelia-bind@kmkdp.com`), member of **`lldap_password_manager` only**, password = new 1Password item **`kdk-cluster/lldap-authelia-bind`** (field `password`).
3. 1Password item **`kdk-cluster/authelia-redis`**, field `password`, 48 alphanumeric characters (no symbols).
4. On kdk-dkr-dmz-01: `sudo chown -R 8091:8091 /opt/kdk-lab/identity/authelia-secrets`.
5. Hash the two plaintext client secrets, one at a time, writing the digest over the plaintext (the apps keep the plaintext):
   ```bash
   docker run --rm -it ghcr.io/authelia/authelia:4.39.28 \
     authelia crypto hash generate pbkdf2 --variant sha512   # paste the existing secret at the prompt
   # copy the "Digest: $pbkdf2-sha512$..." value (without "Digest: ") into
   #   /opt/kdk-lab/identity/authelia-secrets/authelia-app-secrets/grafana_client_secret
   #   /opt/kdk-lab/identity/authelia-secrets/authelia-app-secrets/immich_client_secret
   sudo chown 8091:8091 <file>; sudo chmod 0400 <file>
   ```
   Delete `authelia-app-secrets/netbox_client_secret`.

After merge: deploy order **identity → dmz-proxy → jellyfin → wizarr** (the latter three need `identity_ldap` / `identity_default` to exist), then `RestartStack identity services=[authelia]` if Komodo did not recreate Authelia. Home Assistant host config (`auth_oidc:` in `/opt/kdk-lab/home-assistant/config/configuration.yaml`, not in git): set `features.automatic_user_linking: false` and `roles: {user: ha-users, admin: ha-admins}`, then restart HA. See [[home-assistant]].

Rollback: revert the PR and redeploy identity, dmz-proxy, jellyfin and wizarr. Leave the `8091` ownership as it is; root reads it too.

## Claims policies

`identity_providers.oidc.claims_policies.libreseerr` copies `preferred_username` into the ID Token for client `libreseerr` only. Libreseerr reads the ID Token claims instead of the UserInfo endpoint, so without it SSO users are named by their `sub` UUID. Remove the policy once Libreseerr queries UserInfo (upstream bug).
