---
title: Actual bank sync — setup, token/key rotation, run by hand
---

# Actual bank sync (daily headless Bank Sync)

The [`actual-bank-sync`](../services/actual-bank-sync.md) one-shot presses
Actual's **Bank Sync** once a day. Actual has no server-side scheduler, and login
is **Authelia OIDC only** (no password), so the job authenticates with a
**session token** taken from a browser session, plus the budget's **E2E
password**. Both live in `op://kdk-cluster/actual-banksync`.

> These are hand steps run from the **laptop** (the only place `op` runs). No lab
> host has `op`. `KDK_OP_MODE=sa`, and we only ever `op read` — never
> `op item get` against a vault for a value. A secret value never touches a
> terminal that prints it. In Claude Code, `!` commands get **no TTY**, so create
> the op item in the **1Password app** or in a **normal terminal** with
> `read -rs`.

## 1. Get the session token (dedicated browser session)

The token must come from a session the owner **never logs out of**, so the
owner's normal logout does not break the job. Use a **dedicated Firefox private
window** (or a separate profile) that you sign into once and leave signed in.

1. In that private window, sign in to `https://budget.kmkdp.com` via SSO as
   yourself. **Do not log out of this window afterwards.**
2. Open the dev console (F12 → Console). Run step 1 (it reads the token; `copy()`
   cannot run in the same step because it follows an `await`):

   ```js
   (async()=>{const d=await new Promise((r,j)=>{const q=indexedDB.open('actual');q.onsuccess=()=>r(q.result);q.onerror=j});const v=await new Promise((r,j)=>{const q=d.transaction('asyncStorage').objectStore('asyncStorage').get('user-token');q.onsuccess=()=>r(q.result);q.onerror=j});window.__kdkTok=String(v);return 'ready, length '+window.__kdkTok.length})()
   ```

   It prints `ready, length <n>`. Then run step 2 to copy it and wipe it from the
   page:

   ```js
   copy(window.__kdkTok); delete window.__kdkTok; 'copied'
   ```

   The token is now on the clipboard. Do not paste it into any terminal that
   echoes.

## 2. Get the E2E password

The budget's end-to-end encryption password (Actual → Settings → Encryption).
The owner plans to rotate it after setup — see [§5](#5-when-the-e2e-key-is-rotated).

## 3. Create the op item (no value echoed)

**1Password app:** create a **Password** item named `actual-banksync` in the
**kdk-cluster** vault. Add two fields: `session_token` (paste from the clipboard)
and `e2e_password`. Save. Nothing prints.

**Or a normal terminal** (not a Claude Code `!` line — no TTY there) with
`read -rs` so the value is never on screen or in shell history:

```bash
read -rs 'SESS?session token: '   ; printf '\n'
read -rs 'E2E?e2e password: '     ; printf '\n'
op item create --vault kdk-cluster --category password --title actual-banksync \
  "session_token[password]=$SESS" "e2e_password[password]=$E2E"
unset SESS E2E
```

Verify the fields exist without printing values:

```bash
op item get actual-banksync --vault kdk-cluster --fields label
```

> The stack reads these through `.env.tpl`
> (`op://kdk-cluster/actual-banksync/session_token` and `/e2e_password`), which
> Komodo's `pre_deploy` renders with `op inject`. No git change is involved, so a
> rotation does not auto-redeploy: **run the procedure** (or `DeployStack`) after
> changing a field.

## 4. First run (and clearing the first-deploy alert)

After the stack + procedure are merged and `RunSync` has created them:

1. Komodo → Procedures → **actual-bank-sync** → **Run**. (Or `DeployStack
   actual-bank-sync`.)
2. In Grafana → Explore → Loki, confirm one line:
   `{job="actual-bank-sync"} | json` → a single JSON object with `status="ok"`
   and counts. **No amounts or payees** — counts only.
3. In Actual, confirm new transactions appeared and rules categorised them.

`ActualBankSyncNoSuccess` fires until the first `status="ok"` line exists, so the
first manual run clears it.

## 5. When the session token expires or is logged out

Symptom: alert **ActualBankSyncFailed** with `status=token_expired` (or
`ActualBankSyncNoSuccess` after 36 h). Tokens default to **never** expiring
(`ACTUAL_TOKEN_EXPIRATION` is unset on the server), so this only happens if the
dedicated session was logged out or the token revoked.

1. Reopen the dedicated private window (or sign in fresh if it was closed) and
   **stay signed in**.
2. Re-read the token with the two console snippets in [§1](#1-get-the-session-token-dedicated-browser-session).
3. Update the field, then redeploy:

   ```bash
   read -rs 'SESS?new session token: '; printf '\n'
   op item edit actual-banksync --vault kdk-cluster "session_token[password]=$SESS"; unset SESS
   ```
   Then Komodo → Procedures → **actual-bank-sync** → **Run** (renders `.env`,
   re-runs the job).

## 6. When the E2E key is rotated

Symptom: alert **ActualBankSyncFailed** with `status=decrypt_failure`. The budget
key changed but the stored password did not.

```bash
read -rs 'E2E?new e2e password: '; printf '\n'
op item edit actual-banksync --vault kdk-cluster "e2e_password[password]=$E2E"; unset E2E
```
Then run the procedure again.

## 7. When SimpleFIN is invalid

Symptom: alert **ActualBankSyncSimpleFINInvalid** (`status=simplefin_token_invalid`,
code `INVALID_ACCESS_TOKEN`). The SimpleFIN credential inside Actual expired or
was revoked — **not** an op secret. Re-link SimpleFIN in Actual: open the account
→ bank icon → set up SimpleFIN again with a **new setup token** from the SimpleFIN
site. No op change. Then run the procedure to confirm `status=ok`.

## 8. Run it by hand

- Normal: Komodo → Procedures → **actual-bank-sync** → **Run**, or `DeployStack
  actual-bank-sync`.
- Local dry checks (no real credentials, never connects):

  ```bash
  cd stacks/actual-bank-sync/app && npm ci
  node --test                 # log-formatter + classifier unit tests
  ACTUAL_SERVER_URL=http://actual:5006 \
  ACTUAL_SYNC_ID=821405a7-8bb2-403e-b17e-c8b871a9a670 \
  ACTUAL_SESSION_TOKEN=x ACTUAL_E2E_PASSWORD=y node config-check.js
  ```

## Alternative: a dedicated Actual user (`actual-sync`)

Instead of borrowing the owner's own session, a dedicated identity is cleaner
(the owner's logout never matters). Because login is OIDC-only it needs its own
backend identity:

1. Create an LLDAP user `actual-sync` (identity stack backend) with an email and
   the group the Authelia `two_factor` policy expects; enrol a 2FA factor at
   `sso.kmkdp.com`. *(This is a change to the production identity system — see
   [Actual § second user](../services/actual.md#second-user-wife--rach--do-not-create-without-being-asked);
   do it only when the owner authorises it.)*
2. Sign `actual-sync` into Actual once (claims nothing; owner stays owner) and
   **share the budget** with it.
3. Capture its session token with the [§1](#1-get-the-session-token-dedicated-browser-session)
   snippets and store it in `session_token` as above.

The dedicated **owner** session is the default because it needs no new identity.

## Rollback

The job only reads bank data into Actual; it creates no new infra state and holds
no persistent data (tmpfs). To stop it: set `schedule_enabled = false` on the
`actual-bank-sync` procedure (or remove the `[[procedure]]` and `[[stack]]`
blocks) in `komodo/resources.toml`, push, `RunSync`. Nothing else depends on it;
manual Bank Sync in Actual still works exactly as before.
