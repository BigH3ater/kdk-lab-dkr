---
title: Actual Budget
tags: [homelab/service, finance, lan, oidc]
updated: 2026-09-20
---

# Actual Budget (budget.kmkdp.com)

Envelope/YNAB-style household budgeting for the Macks. **One shared budget file**
(jmack + wife). LAN-only on **kdk-dkr-01**; no DMZ, no Cloudflare tunnel. Remote
phone access is over **Tailscale** (subnet router advertises 10.1.20.0/24; tailnet
DNS resolves `budget.kmkdp.com`). Auth is **Authelia OIDC** (`client_id: actual`).

- Stack: `stacks/actual/compose.yaml` — image `actualbudget/actual-server` (pinned).
- Data: `/opt/kdk-lab/actual/data` (SQLite + sync files) — swept by the nightly
  `/opt/kdk-lab` rsync (`stacks/backup-dkr-01`) and shipped offsite to Proton
  (`stacks/backup-offsite`). No backup change needed.
- Bank sync: **P1 = manual CSV/QFX import** (Chase QFX download). **P2 = SimpleFIN**
  (~$1.50/mo, native in Actual) — deferred, user to review.

## OIDC (Authelia) facts

- Redirect URI to register: `https://budget.kmkdp.com/openid/callback`
- Discovery: `https://sso.kmkdp.com/.well-known/openid-configuration`
- Env (in compose): `ACTUAL_LOGIN_METHOD=openid`, `ACTUAL_OPENID_CLIENT_ID=actual`,
  `ACTUAL_OPENID_SERVER_HOSTNAME=https://budget.kmkdp.com`; secret via `.env` (op-injected).
- **Bootstrap:** first successful OIDC login claims server ownership. Recommended to
  deploy with password login first, create the shared budget, verify, then switch to
  `openid` and have jmack log in first.

## Deploy procedure (permissioned lane — runs against op/hosts/Komodo)

These steps write secrets, edit the fragile Authelia config, and touch hosts, so they
are NOT done by the planning/build subagent. Run them from the permissioned lane.

1. **Create the OIDC secret** (writable vault):
   - store a strong random plaintext at
     `op://kdk-cluster/authelia-app-secrets/actual_oidc_plaintext`.
   - generate the Authelia PBKDF2 hash of that same plaintext:
     `docker exec authelia authelia crypto hash generate pbkdf2 --variant sha512 --password '<plaintext>'`
   - place the **hash** on the DMZ FIRST at
     `/opt/kdk-lab/identity/authelia-secrets/authelia-app-secrets/actual_client_secret`.
2. **Add the Authelia client block** (see below) to
   `stacks/identity/authelia/configuration.yml`, refresh the DMZ identity clone, then
   **validate before restart** (a bad edit crash-loops all SSO):
   `docker exec -e X_AUTHELIA_CONFIG_FILTERS=template authelia authelia config validate`
   — only restart Authelia if validate passes.
3. Commit + push `stacks/actual/` + `komodo/resources.toml` (+ the Authelia change),
   run the `gitops-sync` procedure (or Komodo API), and `DeployStack actual`.
4. Verify (below).

### Authelia client block (add after the `tandoor` client)

```yaml
      - client_id: 'actual'
        client_name: 'Actual Budget'
        client_secret: '{{ secret "/secrets/authelia-app-secrets/actual_client_secret" }}'
        public: false
        redirect_uris:
          - 'https://budget.kmkdp.com/openid/callback'
        scopes:
          - 'openid'
          - 'email'
          - 'profile'
        grant_types:
          - 'authorization_code'
        response_types:
          - 'code'
        authorization_policy: 'two_factor'
        consent_mode: 'auto'
        require_pkce: false
```

## Verify

- Komodo shows `actual` **Deployed / running** (not just Created) on kdk-dkr-01.
- `https://budget.kmkdp.com` serves on the LAN.
- OIDC round-trips through `sso.kmkdp.com` for jmack; first login claims ownership.
- The shared budget file is creatable; both users open the same file.
- Remote: `budget.kmkdp.com` resolves and loads over Tailscale off-home.

## Second user (wife / "rach") — do NOT create without being asked

OIDC login requires an Authelia identity. Creating one means adding a user to the
Authelia user backend (the file-based `users_database.yml` referenced by the identity
stack, or LLDAP if that is the backend) with username, hashed password, email, and the
group the `two_factor` policy expects — then the user enrolls a 2FA factor at
`sso.kmkdp.com`. This is a change to the production identity system and is left for the
user to authorize explicitly.
