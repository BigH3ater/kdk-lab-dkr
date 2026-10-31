---
title: Rotate a secret
---

# Rotate a secret

1Password is the source of truth (`kdk-cluster` for service secrets, `kdk-ops`
for operator credentials). The `op` CLI runs **only on the laptop**; no lab host
has it. Values reach a host over ssh stdin and are never printed, pasted into a
terminal on the host, or committed.

Naming: `{service}-{credential-type}`, value in the field `password` (older
items use `credential`: check with
`op item get <item> --vault kdk-cluster --fields label`).

## 1. Create or update the item (laptop)

```bash
# a generated value
op item create --vault kdk-cluster --category password --title <service>-<type> \
  --generate-password=32,letters,digits
# a value you generate yourself
op item create --vault kdk-cluster --category password --title <service>-<type> \
  password="$(openssl rand -hex 32)"
# rotate an existing item
op item edit <item> --vault kdk-cluster password="$(openssl rand -hex 32)"
# a token minted in an app's UI: paste it into the item, never into a host file
op item edit <item> --vault kdk-cluster password=<token from the UI>
```

## 2. Get it to the consumer

Pick by how the stack reads the secret.

| Consumer | How it gets the value | Step |
|---|---|---|
| `.env.tpl` with `op://` refs (most stacks on kdk-dkr-01 and kdk-dkr-dmz-01) | Komodo `pre_deploy` runs `op inject` against 1Password Connect | **Deploy** the stack in Komodo (a rotation has no git change, so the 15-min loop does not redeploy it). The changed env recreates the container |
| Host-managed env or secret file (Pis, kdk-mon-01, kdk-log-01: no op.env) | root 600 file on the host | `op read` over ssh stdin (below), then `RestartStack` |
| File mounted with compose `configs:` or a single-file bind (Authelia secret files, Alertmanager token files) | read at container start | write the file (below), then `RestartStack <stack> services=[<svc>]`: compose does not hash file contents |
| `pre_deploy` `op read` into the clone (backup-collect) | rendered on every deploy | Deploy the stack |
| A credential stored inside an app (Node-RED server node, Scrypted plugin) | the app's own UI | paste in the UI, then `op item edit` |

Write a host file without the value touching the screen:

```bash
op read op://kdk-cluster/<item>/password \
  | ssh kdkadmin@<host> 'sudo install -m 600 -o root -g root /dev/stdin <path>'
# an env file with several keys
printf 'KEY_A=%s\nKEY_B=%s\n' "$(op read op://kdk-cluster/<a>/password)" "$(op read op://kdk-cluster/<b>/password)" \
  | ssh kdkadmin@<host> 'sudo install -m 600 -o root -g root /dev/stdin /opt/kdk-lab/<stack>/<name>.env'
```

`$(op read …)` expands on the laptop; `install … /dev/stdin` runs on the host.
Values with `$` (bcrypt hashes) go into an env file in **single quotes**, or
compose eats the `$` segments.

## 3. Hashes

When the app wants a hash, compute it **inside the app's container** from stdin
and store the hash in the same 1Password item:

```bash
HASH=$(op read op://kdk-cluster/node-red-admin/password \
  | ssh kdkadmin@10.1.30.21 "sudo docker exec -i node-red node -e \"process.stdin.on('data',d=>console.log(require('bcryptjs').hashSync(d.toString().trim(),8)))\"")
echo "hash bytes: ${#HASH}"
op item edit node-red-admin --vault kdk-cluster "hash[password]=$HASH"
```

Authelia client secrets: the plaintext goes to the client app, the pbkdf2
digest to Authelia's secret file **and** the matching `authelia-app-secrets`
field. `authelia crypto hash generate` cannot read stdin (`--password` only, which
lands in the host's argv), so build the digest on the laptop and let Authelia
check it. Authelia (go-crypt) encodes salt and key in the **crypt base64
alphabet** (`+` becomes `.`, no padding); a standard-base64 digest is rejected at
startup and Authelia crash-loops (2026-10-29: 3 min SSO outage).

```bash
op read op://kdk-cluster/<app>-oidc/password | tr -d '\n' | python3 -c '
import sys,os,hashlib,base64
pw=sys.stdin.read().encode(); salt=os.urandom(16); it=310000
dk=hashlib.pbkdf2_hmac("sha512",pw,salt,it,64)
ab64=lambda x: base64.b64encode(x).decode().rstrip("=").replace("+",".")
print("$pbkdf2-sha512$%d$%s$%s"%(it,ab64(salt),ab64(dk)))' > /tmp/<app>.digest
# prove it before it touches Authelia (dummy or real password, never echoed):
op read op://kdk-cluster/<app>-oidc/password | ssh kdkadmin@192.168.191.20 \
  "P=\$(cat); sudo docker run --rm --entrypoint authelia \$(sudo docker inspect authelia --format '{{.Config.Image}}') \
   crypto hash validate '$(cat /tmp/<app>.digest)' --password \"\$P\""   # -> The password matches the digest.
op item edit authelia-app-secrets --vault kdk-cluster "<app>_client_secret=$(cat /tmp/<app>.digest)"
op read op://kdk-cluster/authelia-app-secrets/<app>_client_secret | tr -d '\n' \
  | ssh kdkadmin@192.168.191.20 'sudo install -m 600 -o 8091 -g 8091 /dev/stdin /opt/kdk-lab/identity/authelia-secrets/authelia-app-secrets/<app>_client_secret'
# then the plaintext into the app, and RestartStack identity (service authelia)
```

Stdin gotcha: a script fed as a heredoc (`python3 - <<EOF`) owns stdin, so
`sys.stdin.read()` returns nothing and you write an empty value (Libreseerr
2026-10-29). Pipe the value into `python3 -c '...'` or stage the script in a file.

## 4. Verify without printing the value

```bash
ssh kdkadmin@<host> 'sudo stat -c "%a %U %s %n" <path>'                       # 600 root, non-zero bytes
ssh kdkadmin@<host> 'sudo docker exec <ctr> env | grep -c "^<KEY>="'           # 1 (names only)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $(op read op://kdk-cluster/<item>/password)" <url>   # 200
curl -s -o /dev/null -w '%{http_code}\n' <url>                                 # 401 without it
```

Then revoke the old value in the issuing system (app UI, GitHub, Cloudflare) and
check the old one fails (401).

## 5. Things that leak

- Komodo's "Compose Config" stage writes interpolated env values into the
  stack's update log. Treat Komodo update logs as secret-bearing.
- A token in a git clone URL (`https://<token>@github.com/...`) sits in
  `.git/config` on the host. Prefer Komodo's git-provider account; give the token
  **read-only** Contents on the named repos only, with an expiry.
- `docker inspect` shows every env var. Never paste its output into a PR or issue.
- An env file shared by several services hands every secret in it to each of
  them. Use one env file per service (PR #62 split Node-RED and Z2M off Traefik's
  `secrets.env`).

## Examples from 2026-10-28

| Secret | Pattern |
|---|---|
| `node-red-admin`, `node-red-credential-secret`, `zigbee2mqtt-frontend-token` | `op item create`, hash via `docker exec -i`, env files via `install /dev/stdin` on kdk-dkr-02 |
| `home-assistant-nodered-token` | minted in HA as user `node-red`, pasted in the Node-RED server node, `op item edit` |
| `vikunja-api-ro` | new token in the Vikunja UI → 1Password → `dashboard` redeploy, old token revoked |
| `vikunja-api-approvals` | the Home Assistant stack's Vikunja token (approvals + robot-vacuum chores). New token in the Vikunja UI (scopes in `approval-tasks.md`) → 1Password → three places on kdk-dkr-02: `vikunja_approvals_auth` in HA `secrets.yaml`, HA `/config/.vikunja_token` (chore sensor), and the three Node-RED HTTP nodes `h_vk1`–`h_vk3` (bearer credential, set in the Node-RED editor) → restart HA and Node-RED → old token revoked |
| `komodo-github-pat` | new fine-grained PAT → 1Password + the Komodo git account; PullStack OK, old token 401 |
| `authelia-app-secrets/grafana_oidc_plaintext` | captured from the Authelia secret file into 1Password, then written to mon-01's `.env` from `op read` |

## Examples from 2026-10-29

| Secret | Pattern |
|---|---|
| `kdk-ops/libreseerr-admin` | new value in 1Password (owner's personal `op` session: the service account cannot write `kdk-ops`), werkzeug scrypt hash via `docker exec -i`, `users.json` rewritten with the container stopped ([Seerr page](../services/seerr.md#libreseerr-admin-reset)) |
| `kdk-ops/trailarr-admin` | `op item create`, applied once through `WEBUI_PASSWORD` on a one-off container ([Media page](../services/media.md#trailarr)) |
| `kdk-cluster/libreseerr-oidc` + `authelia-app-secrets/libreseerr_client_secret` | plaintext item, digest built locally in crypt base64 and validated with `authelia crypto hash validate`, file from `op read`, Libreseerr `config.json`, Authelia restart |

## Examples from 2026-10-31

| Secret | Pattern |
|---|---|
| `kdk-cluster/actual-banksync` (`session_token`, `e2e_password`) | `.env.tpl` with `op://` refs (op inject). `session_token` from a dedicated never-logged-out Firefox session (console → IndexedDB `actual` → `asyncStorage` → `user-token`); `e2e_password` is the budget E2E key. Create in the 1Password app or `read -rs` (Claude Code `!` lines have no TTY). After `op item edit`, run the `actual-bank-sync` procedure to re-render `.env`. Not a host file and not a hash. Full steps: [Actual bank sync](actual-bank-sync.md). SimpleFIN's own credential is **not** here — it lives inside Actual (re-link in the app) |
