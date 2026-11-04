---
title: Actual Budget
tags: [homelab/service, finance, lan, oidc]
updated: 2026-11-04
---

# Actual Budget (budget.kmkdp.com)

Envelope/YNAB-style household budgeting for the Macks. **One shared budget file**
(jmack + wife), kept in Actual's **Envelope** budgeting mode and **end-to-end (E2E)
encrypted**. LAN-only on **kdk-dkr-01**; no DMZ, no Cloudflare tunnel. Remote
phone access is over **Tailscale** (subnet router advertises 10.1.20.0/24; tailnet
DNS resolves `budget.kmkdp.com`). Auth is **Authelia OIDC** (`client_id: actual`).

| Field | Value |
|---|---|
| Image | `docker.io/actualbudget/actual-server:26.10.0` (digest-pinned; running 26.10.0 on 2026-11-04). Renovate PR #305 proposes 26.11.0 (see Upgrades) |
| Stack | `stacks/actual/compose.yaml`, Komodo stack `actual` (`deploy = true`, `pre_deploy` `op inject` of `.env.tpl`) |
| Data | `/opt/kdk-lab/actual/data` (SQLite `server-files/account.sqlite` + the encrypted sync file under `user-files/`) |
| Backup | swept by the nightly `/opt/kdk-lab` rsync (`stacks/backup-dkr-01`) and shipped offsite (`stacks/backup-offsite`). The sync file is encrypted with the E2E key, so **a restore is only useful with that key** (1Password, `kdk-ops/actual-e2e`) |
| Bank sync | **SimpleFIN** (native in Actual), linked at the server level. A person presses **Bank Sync**. By owner decision (2026-10-31) **no automated job writes to the budget**: the daily `actual-bank-sync` one-shot (#263) was retired before it ever ran. Manual CSV/QFX import still works as a fallback |

Budget contents, figures and the budget agents' runs live only in the vault
(`1. Projects/household-budget/`), never in this repo.

## Verify

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://budget.kmkdp.com/          # 200 (LAN or tailnet)
ssh kdkadmin@10.1.20.20 'sudo docker ps --filter name=actual --format "{{.Image}} {{.Status}}"'
#   actualbudget/actual-server:26.10.0 Up ...
# E2E still on (counts only; opens the server DB read-only):
ssh kdkadmin@10.1.20.20 'sudo python3 -c "import sqlite3;c=sqlite3.connect(\"file:/opt/kdk-lab/actual/data/server-files/account.sqlite?mode=ro\",uri=True);print(c.execute(\"select count(*),sum(encrypt_keyid is not null) from files where deleted=0\").fetchone())"'
#   (1, 1)  = one live budget file, encrypted
```

Then in the browser: the login goes through `sso.kmkdp.com` and the shared budget opens.

## OIDC (Authelia) facts

- Redirect URI: `https://budget.kmkdp.com/openid/callback` (client `actual` in
  `stacks/identity/authelia/configuration.yml`, `two_factor`, no PKCE)
- Discovery: `https://sso.kmkdp.com/.well-known/openid-configuration`
- Env (in compose): `ACTUAL_LOGIN_METHOD=openid`, `ACTUAL_OPENID_CLIENT_ID=actual`,
  `ACTUAL_OPENID_SERVER_HOSTNAME=https://budget.kmkdp.com`,
  `ACTUAL_OPENID_DISCOVERY_URL`; the client secret via `.env` (op-injected from
  `op://kdk-cluster/authelia-app-secrets/actual_oidc_plaintext`; Authelia holds the
  pbkdf2 digest in `.../authelia-app-secrets/actual_client_secret` on the DMZ).
- The first OIDC login claimed server ownership (jmack, 2026-09-20). If OIDC ever
  breaks: set the login method to password and redeploy.

## Upgrades

- Before merging an Actual image bump, take one copy that does not depend on the
  nightly mirror (the rsync is `--delete`, so a bad upgrade night is mirrored):
  *Settings -> Export* in Actual (a decrypted zip, kept on the laptop), or a read-only
  `sudo tar` of `/opt/kdk-lab/actual/data`.
- **26.11.0 (PR #305, open):** a SimpleFIN bridge on a private address needs
  `ACTUAL_SIMPLEFIN_ALLOWED_NETWORKS` (not this household: it uses the public bridge).
  A new per-client setting syncs linked accounts on an interval while Actual is open;
  leave it **off** (no automated writer). Payees, Rules, Bank Sync and Tags move from
  the sidebar into Settings. After the merge: one manual Bank Sync, and check the
  auto-sync setting is off.

## History

Deployed 2026-09-20 (commit db05af4) with the client block above and the
first-login ownership claim. The original permissioned-lane install steps (secret
creation, Authelia client block, validate-before-restart) are in git history of this
page (`git log -p docs/services/actual.md`); use [[rotate-a-secret]] for the client
secret today.

## Second user (wife / "rach") — do NOT create without being asked

OIDC login requires an Authelia identity. Authelia's user backend is **LLDAP**
([[identity]]): creating one means an LLDAP account at `users.kmkdp.com`, then the
user enrolls a 2FA factor at `sso.kmkdp.com` (the `actual` client is `two_factor`).
This is a change to the production identity system and is left for the owner to
authorize explicitly.
