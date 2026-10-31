# actual-bank-sync

Daily headless **Bank Sync** for [Actual Budget](../actual/). Actual never syncs
bank data on its own; SimpleFIN transactions only arrive when someone presses
*Bank Sync*. This one-shot presses it once a day (05:32 America/Chicago, the
`actual-bank-sync` Komodo procedure) and exits.

- **Node 24** one-shot, `@actual-app/api` **26.10.0** (matches `actual-server`
  26.10.0), built locally (`kdk/actual-bank-sync`), see [`app/`](app/).
- **Auth:** a session token from a dedicated, never-logged-out browser session of
  the owner's own OIDC user (login is Authelia OIDC only -- no password). The
  budget is end-to-end encrypted, so it also needs the E2E password. Both are
  op-injected into `.env` from `op://kdk-cluster/actual-banksync` (`.env.tpl`).
- **No financial data leaves the host.** The decrypted budget lives only on a
  tmpfs. Each run logs **one JSON line of counts only** (accounts synced, new
  transactions, how many rules categorised, errors, duration) -- never amounts,
  payees, account names, balances or notes. The line goes to stdout **and**
  straight to Loki (`job="actual-bank-sync"`), because the host Alloy does not
  reliably scrape one-shot containers.
- **Alerting:** the Loki ruler ([`stacks/loki/rules/actual-bank-sync.yml`](../loki/rules/actual-bank-sync.yml))
  pages via Alertmanager -> Pushover on `status != ok` or no successful run in
  36 h.

Setup, token/key rotation, and how to run it by hand:
[`docs/runbooks/actual-bank-sync.md`](../../docs/runbooks/actual-bank-sync.md).
Service reference: [`docs/services/actual-bank-sync.md`](../../docs/services/actual-bank-sync.md).

## Test locally (no real credentials)

```bash
cd app
npm ci
node --test            # log-formatter + classifier unit tests
# dry config check (placeholder values; never connects):
ACTUAL_SERVER_URL=http://actual:5006 \
ACTUAL_SYNC_ID=821405a7-8bb2-403e-b17e-c8b871a9a670 \
ACTUAL_SESSION_TOKEN=x ACTUAL_E2E_PASSWORD=y node config-check.js
```
