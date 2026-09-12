---
title: 1Password Connect
host: kdk-dkr-01
stack: op-connect
tier: infra
status: live
storage: yes
secrets: [1password-credentials.json]
updated: 2026-09-12
tags: [homelab/stack, tier/infra, status/live]
---

# 1Password Connect

Unmetered secrets path for every stack's `op inject` pre-deploy; reads the `kdk-cluster` and `kdk-ops` vaults.

**Related:** [[kdk-dkr-01]] · [[komodo]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Local Connect API so stacks render `.env` from `op://` refs without metered SA calls |
| Status | 🟢 Live — `/heartbeat` returns `.` |
| Host | [[kdk-dkr-01]], bound `10.1.20.20:8080` (bearer token only) |
| Images | connect-api + connect-sync `1.8.2` |
| Deploy | Komodo stack `op-connect` (git-linked) |
| Storage | credentials file + a `data` volume (encrypted vault replica) |
| Blast radius | Secret-rendering stacks can't deploy; running containers unaffected |

## Verify

```bash
ssh kdkadmin@10.1.20.20 'curl -fsS http://10.1.20.20:8080/heartbeat'   # .
docker ps --filter name=onepassword-connect --format '{{.Names}} {{.Status}}'  # api + sync Up
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| connect-sync loops "unexpected end of JSON input" | credentials file wrong owner | `stat -c '%u:%g %a' /etc/kdk-lab/1password-credentials.json` → `999:999 600` |
| `op inject` 401 during a deploy | token lacks the vault | token must carry both `kdk-cluster` and `kdk-ops` (doubled `--vaults` flags at creation) |
| Heartbeat refused from another host | bound to VLAN-20 address only | reach it from `10.1.20.20`, not `0.0.0.0` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | operator-seeded credentials file | server won't start |
| Needed by | [[komodo]] pre_deploy on both VMs | `op inject` fails; secret stacks won't deploy |

## Observability

No dashboard. Health is the `/heartbeat` endpoint; no alert wired.

## Architecture

`connect-api` publishes only on `10.1.20.20:8080`; `connect-sync` has no published port and shares the `data` volume. Komodo periphery hosts read the token from `/etc/komodo/op.env` and call the API over VLAN 20 (DMZ host reaches it through the allowed `192.168.191.20 → 10.1.20.20:8080` rule).

## Configuration

| Path / setting | What it is |
|---|---|
| `/etc/kdk-lab/1password-credentials.json` | Connect credentials, `chown 999:999`, mode `0600`, operator-seeded |
| `/etc/komodo/op.env` (each host) | `OP_CONNECT_HOST` + `OP_CONNECT_TOKEN` used by `op inject` |

## Secret Rotation

| Secret (1P item) | Used for | Class | Rotate live? |
|---|---|---|---|
| Connect credentials file | Vault decryption | 🔴 never on live | regenerate the Connect server in 1P, reseed the file, re-mint the token, redistribute `op.env` |
| Connect token | API bearer | 🟡 needs re-mint | new token must include `--vaults kdk-cluster --vaults kdk-ops` (separate flags) |

Compromise of the credentials file means a decryptable replica of both vaults — delete the server and rotate everything.

## User Guide

N/A — headless API.
