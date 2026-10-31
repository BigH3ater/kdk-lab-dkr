---
title: actual-bank-sync — daily headless Bank Sync for Actual
host: kdk-dkr-01
stack: actual-bank-sync
tier: app
status: live
storage: no
secrets: [actual-banksync]
updated: 2026-10-31
tags: [homelab/stack, tier/app, status/live, finance]
---

# actual-bank-sync — daily headless Bank Sync for Actual

Daily one-shot that runs Actual Budget's **Bank Sync** without anyone pressing a
button. Actual does not sync bank data on its own (its docs say so): SimpleFIN
transactions only arrive when a logged-in client runs Bank Sync. There is no
server-side scheduler, so this job is the scheduler.

## At a glance

- **Runs:** once a day, **05:32 America/Chicago**, via the `actual-bank-sync`
  Komodo procedure (`DeployStack`, `destroy_before_deploy` so it re-runs fresh).
- **Image:** `kdk/actual-bank-sync`, built locally from
  [`stacks/actual-bank-sync/app`](https://github.com/BigH3ater/kdk-lab-dkr/tree/main/stacks/actual-bank-sync/app)
  (Node 24, `@actual-app/api` **26.10.0** = the `actual-server` 26.10.0 version).
- **Talks to:** `http://actual:5006` over the `actual` stack's Docker network — no
  proxy, no TLS hop, stays on the host.
- **Auth:** a **session token** from a dedicated, never-logged-out browser session
  (login is Authelia OIDC only — there is no password to automate). The budget is
  end-to-end encrypted, so it also needs the **E2E password**. See
  [Secret rotation](#secret-rotation).
- **Output:** exactly **one JSON line of counts** per run (stdout + a direct push
  to Loki, `job="actual-bank-sync"`). Rules in Actual run on imported
  transactions, so imports get categorised and tagged automatically.

## Privacy — what never leaves the host

The household's financial data must not reach Loki. The job logs **counts only**:
`accounts_total`, `accounts_ok`, `accounts_failed`, `new_transactions`,
`matched_transactions`, `updated_accounts`, `rules_categorised`,
`reauth_required`, `status`, `error_code`, `duration_ms`. It **never** logs
amounts, payees, account names, balances or notes. The log line is assembled from
a strict allow-list (`lib.js` `ALLOWED_KEYS`), guaranteed by a unit test, so even
a raw account object passed by mistake is stripped. The decrypted budget lives
only on a **tmpfs** and is gone when the container exits.

## Status values

| `status` | Meaning | Action |
|---|---|---|
| `ok` | All linked accounts synced. | none |
| `partial` | Some accounts synced, some failed. | check the failed account(s) in Actual |
| `simplefin_token_invalid` | SimpleFIN access token dead/revoked (`INVALID_ACCESS_TOKEN`); nothing can sync. | **re-link SimpleFIN** in Actual (new setup token) |
| `token_expired` | The Actual session token expired or was logged out. | renew the browser session + update `session_token` |
| `decrypt_failure` | Wrong E2E password (usually a rotated budget key). | update `e2e_password` |
| `network_failure` | Could not reach the Actual server. | check the `actual` stack |
| `config_error` | Missing/invalid env. | check `.env.tpl` / op item |
| `error` | Other failure. | see the runbook |

## Observability / alerting

Loki ruler file [`stacks/loki/rules/actual-bank-sync.yml`](https://github.com/BigH3ater/kdk-lab-dkr/tree/main/stacks/loki/rules/actual-bank-sync.yml)
pages via Alertmanager → Pushover:

- **ActualBankSyncSimpleFINInvalid** — `status="simplefin_token_invalid"`; message
  says to re-link SimpleFIN.
- **ActualBankSyncFailed** — any other non-ok status; names the status + code.
- **ActualBankSyncNoSuccess** — no `status="ok"` line in **36 h** (two missed
  windows). This is the safety net that would have caught the accounts sitting
  un-synced since 2026-09-20: an expired token or a rotated E2E key that was not
  updated stops producing `ok` lines, and 36 h later this fires.

## Dependencies

- The [`actual`](actual.md) stack (its `actual_default` Docker network and the
  server on `:5006`).
- SimpleFIN linked inside Actual (the `actual-banksync` op item holds the session
  token + E2E password, **not** the SimpleFIN credential — that lives in Actual).
- Loki on [kdk-log-01](../hosts/kdk-log-01.md) for the status line + alerts.

## Configuration

Non-secret env is in [`compose.yaml`](https://github.com/BigH3ater/kdk-lab-dkr/tree/main/stacks/actual-bank-sync/compose.yaml):
`ACTUAL_SERVER_URL=http://actual:5006`, the budget `ACTUAL_SYNC_ID` (not a
secret), `ACTUAL_DATA_DIR=/data` (tmpfs), `LOKI_URL`, `NODE_NAME`. Secrets come
from `.env` (op-injected from `.env.tpl`): `ACTUAL_SESSION_TOKEN`,
`ACTUAL_E2E_PASSWORD`.

## Secret rotation

Full steps: [runbook](../runbooks/actual-bank-sync.md). In short — the session
token comes from a **dedicated browser session that is never logged out** (so the
owner's normal logout does not break the job); the E2E password must be updated
in `op://kdk-cluster/actual-banksync/e2e_password` whenever the budget key is
rotated.

## Why a session token (token expiry)

With `ACTUAL_TOKEN_EXPIRATION` unset on the server, Actual session tokens default
to **never** expiring — a token lasts until it is logged out or revoked. So a
dedicated private-window session that the owner signs in once and never logs out
of keeps working indefinitely. A **dedicated Actual user** (`actual-sync`) is a
cleaner alternative but needs its own LLDAP/Authelia identity because login is
OIDC-only; the dedicated-session approach is the default because it is simpler.
See the runbook for both.
