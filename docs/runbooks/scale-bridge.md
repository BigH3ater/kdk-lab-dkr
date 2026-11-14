---
title: scale-bridge — host files, deploy, first weigh-in, go live, rollback
updated: 2026-11-14
---

# scale-bridge

Owner steps for [scale-bridge](../services/scale-bridge.md) (Body Pod 2 → wger) on
kdk-dkr-02. Steps 1–3 come **before** the PR is merged: the deploy fails while either
env file is missing. Nothing here prints a key: values travel from 1Password over SSH
stdin and are checked by byte length.

## 1. Host directory

**Laptop (SSH agent)**

```bash
ssh kdkadmin@10.1.30.21 'sudo install -d -o root -g root -m 0750 /opt/kdk-lab/scale-bridge'
```

## 2. `secrets.env` from 1Password

No new secret: the per-person wger keys already exist (`wger-api-jmack`,
`wger-api-rach`, made in [wger runbook step 8](wger.md#8-api-key-per-person--1password)).
A rotated key goes into 1Password first (that step), then this step again.

**Laptop (`op` signed in, SSH agent)**

```bash
{
  printf 'WGER_API_KEY_JMACK=%s\n' "$(op read op://kdk-cluster/wger-api-jmack/credential)"
  printf 'WGER_API_KEY_RACH=%s\n'  "$(op read op://kdk-cluster/wger-api-rach/credential)"
} | ssh kdkadmin@10.1.30.21 'sudo install -m 600 -o root -g root /dev/stdin /opt/kdk-lab/scale-bridge/secrets.env'
# verify: two lines, each key 40 characters; prints names and lengths only
ssh kdkadmin@10.1.30.21 'sudo awk -F= "{print \$1, length(\$2)}" /opt/kdk-lab/scale-bridge/secrets.env; sudo stat -c "%U:%G %a" /opt/kdk-lab/scale-bridge/secrets.env'
#   WGER_API_KEY_JMACK 40 / WGER_API_KEY_RACH 40 / root:root 600
```

`printf` is a shell builtin, so the keys are never in a process argument list. If
`op read` says the field does not exist, the item stores the key under `password`: use
`.../password` instead.

Relay mode later: put the proxy's API encryption key in 1Password first (item
`esphome-scale-proxy-noise`, created from stdin the way [Rotate a secret](rotate-a-secret.md)
does, never as a command argument), then add a third line
`printf 'ESPHOME_NOISE_SCALE_PROXY=%s\n' "$(op read op://kdk-cluster/esphome-scale-proxy-noise/password)"`
inside the braces above and rerun the step.

## 3. `scale-bridge.env` (people bands)

The bands are health data: this file is the only place they live (plus the vault if you
want a note). Shape: [`stacks/scale-bridge/scale-bridge.env.example`](https://github.com/BigH3ater/kdk-lab-dkr/blob/main/stacks/scale-bridge/scale-bridge.env.example).

**Laptop (SSH agent, VS Code)**

```bash
ssh kdkadmin@10.1.30.21 'sudo install -m 600 -o kdkadmin -g kdkadmin /dev/null /opt/kdk-lab/scale-bridge/scale-bridge.env'
code --remote ssh-remote+kdkadmin@10.1.30.21 /opt/kdk-lab/scale-bridge/scale-bridge.env
```

In VS Code, write (one line for the JSON, in single quotes):

```text
SCALE_BRIDGE_PEOPLE='[{"name":"jmack","min_kg":<lo>,"max_kg":<hi>,"token_env":"WGER_API_KEY_JMACK"},{"name":"rach","min_kg":<lo>,"max_kg":<hi>,"token_env":"WGER_API_KEY_RACH"}]'
SCALE_ADDRESS=
SCALE_BRIDGE_ESPHOME_PROXIES=
```

- Bands: kg, inclusive, a few kg either side of each person's recent weight, and **not
  overlapping** (a weight in two bands is written nowhere).
- `SCALE_ADDRESS`: optional, the scale's address from the vault capture note. Empty =
  any device named `Body Pod 2`.
- `SCALE_BRIDGE_ESPHOME_PROXIES`: empty until a proxy exists (`<ip>=ESPHOME_NOISE_SCALE_PROXY`).

Save, then hand the file back to root:

```bash
ssh kdkadmin@10.1.30.21 'sudo chown root:root /opt/kdk-lab/scale-bridge/scale-bridge.env && sudo chmod 600 /opt/kdk-lab/scale-bridge/scale-bridge.env && sudo stat -c "%U:%G %a" /opt/kdk-lab/scale-bridge/scale-bridge.env'
#   root:root 600
```

## 4. Deploy (Komodo)

Merge the PR. The `gitops-sync` procedure (every 15 min) creates the stack and deploys
it; to go now: **komodo.kmkdp.com → Procedures → gitops-sync → Run**, or **Stacks →
scale-bridge → Deploy**. The deploy builds `kdk/scale-bridge:0.1.0` on the Pi (a few
minutes the first time).

**Laptop (SSH agent)**

```bash
ssh kdkadmin@10.1.30.21 'sudo docker ps --filter name=scale-bridge --format "{{.Names}} {{.Status}}"'
#   scale-bridge Up ... (healthy)          (health starts after ~1 min)
ssh kdkadmin@10.1.30.21 'sudo docker logs scale-bridge 2>&1 | head -5'
#   {"event":"config","dry_run":true,"people":["jmack","rach"],...}
#   {"event":"token_check","person":"jmack","result":"ok","status":200}   (and rach)
#   {"event":"scanning","transport":"local","dry_run":true,...}
```

`token_check` `missing` or `rejected` = step 2. `config_invalid` = step 3 (the error names
the field).

## 5. First dry-run weigh-in

The scale must be in radio range of kdk-dkr-02 (today: carry it up from the basement, as
for the 2026-11-14 capture, until a relay exists). Bluetooth **off** on both phones: a
phone with the Hume app takes the scale first.

1. **Bathroom/scale:** step on, hold the handle until the display locks (~20 s), step off.
2. **Laptop:**

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs --since 5m scale-bridge 2>&1 | grep -E "\"event\":\"(session|attribution|would_write)\""'
```

Expected, in order:

| Line | Fields to check |
|---|---|
| `session` | `result: reading`, `ended_by: complete`, `settled: true`, `settled_state: "05"`, `impedance_frames: 2`, `extended_frames: 4`, `bad_frames: 0` |
| `attribution` | `result: matched`, `person: <you>`, `impedance: true` |
| `would_write` | `weight_kg` = the display, `impedance_values: 10`, 11 `categories` |

Then the other person. `ended_by: impedance_timeout` with `impedance_frames: 0` = handle
let go too early (a weight-only reading). The `would_write` line is the only place the
weight is logged.

## 6. Switch dry-run off

A one-line PR: `stacks/scale-bridge/compose.yaml`, `SCALE_BRIDGE_DRY_RUN: "false"`. Merge,
deploy (step 4), weigh in once.

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs --since 5m scale-bridge 2>&1 | grep -E "\"event\":\"(wrote|write_failed)\""'
#   first ever: {"event":"wrote","person":"jmack","created":11,"exists":0,"categories_created":10,...}
#   later:      created 11, categories_created 0
```

**wger web (`https://wger.kmkdp.com`, as that person) → Measurements:** Body weight has the
new entry; ten `Impedance … kHz …` categories exist, unit `ohm`. A `write_failed` line
retries by itself (30 s, doubling, 6 tries); `write_gave_up` = enter that weight in the app.

## Rollback

- **Stop it:** Komodo → Stacks → scale-bridge → Destroy. To keep it gone, revert the PR
  (the `[[stack]]` entry) so the next sync does not redeploy it.
- **Back to dry-run only:** revert the step 6 PR, deploy.
- **Left behind:** `/opt/kdk-lab/scale-bridge/` (remove with
  `ssh kdkadmin@10.1.30.21 'sudo rm -r /opt/kdk-lab/scale-bridge'`), the image
  (`sudo docker image rm kdk/scale-bridge:0.1.0`), and in wger the written entries and the
  ten impedance categories (delete per person in the web UI; deleting a category deletes
  its entries). The wger keys stay: other tools use them.
