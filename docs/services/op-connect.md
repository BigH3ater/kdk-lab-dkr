---
title: 1Password Connect
---
# 1Password Connect

Unmetered secrets path for every stack's `op inject` pre-deploy.

| | |
|---|---|
| Host | kdk-dkr-01, bound 10.1.20.20:8080 (bearer token) |
| Stack | `stacks/op-connect` — connect-api + connect-sync 1.8.2 |
| Credential | /etc/kdk-lab/1password-credentials.json, chown 999:999 mode 600, operator-seeded |
| Consumers | Komodo pre_deploy on both VMs via /etc/komodo/op.env |
| Blast radius | compromise = decryptable replica of both vaults → delete server, rotate everything |

Verify on host: `curl -fsS http://10.1.20.20:8080/heartbeat` → `.`

Gotcha: credentials file must be uid/gid 999 or connect-sync loops
"unexpected end of JSON input".
