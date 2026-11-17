---
title: scale-bridge — host files, deploy, first weigh-in, engine, live writes, engine to wger, raw captures, relay, rollback
updated: 2026-11-16
---

# scale-bridge

Owner steps for [scale-bridge](../services/scale-bridge.md) (Body Pod 2 → wger) on
kdk-dkr-02. Steps 1–3 come **before** the PR is merged: the deploy fails while either
env file is missing. The captures directory in step 1 (new in 0.1.3) also comes before the
merge: without it the deploy still succeeds, but nothing is captured (see step 1). Nothing here prints a key: values travel from 1Password over SSH
stdin and are checked by byte length.

**Upgrading to 0.2.0 (body-composition engine), in this order:** 2a (key into 1Password),
2b (`LEFU_SECRET_8` into `secrets.env`), 3 (profiles into `scale-bridge.env`), then merge and
4 (deploy), then [5b](#5b-attended-first-engine-run) (the attended first engine run). Steps
2a–3 are optional for the deploy: without the key the engine switches itself off and the
bridge works exactly as 0.1.3.

**Upgrading to 0.3.0 (engine results + heart rate to wger):** no host step; merge = deploy,
then [6b](#6b-engine-results-and-heart-rate-to-wger-030).

**Upgrading to 0.3.1 (five derived values to wger):** no host step; merge = deploy, then
[6c](#6c-derived-values-to-wger-031).

## 1. Host directories

**Laptop (SSH agent)**

```bash
ssh kdkadmin@10.1.30.21 'sudo install -d -o root -g root -m 0750 /opt/kdk-lab/scale-bridge'
# raw capture files (health data): writable by the container uid 65534 only, readable by root
ssh kdkadmin@10.1.30.21 'sudo install -d -o 65534 -g root -m 0750 /opt/kdk-lab/scale-bridge/captures && sudo stat -c "%u:%g %a %n" /opt/kdk-lab/scale-bridge/captures'
#   65534:0 750 /opt/kdk-lab/scale-bridge/captures
```

`install -d` on an existing directory only resets its owner and mode, so rerunning it is
safe (also the fix for a directory Docker created). The parent stays `root:root 0750`: the
container gets `captures/` itself as a bind mount, so it never needs the parent.

If the captures directory is missing at deploy time, Docker creates it as an empty
`root:root 0755` directory (short-syntax bind mount) and the deploy **succeeds**; uid 65534
cannot write there, so the bridge logs one `capture_unavailable` error at startup
(`problem: not_writable`) and runs without capturing. Fix: run the second command above; the
next weigh-in is captured, no restart needed.

## 2. `secrets.env` from 1Password

No new secret: the per-person wger keys already exist (`wger-api-jmack`,
`wger-api-rach`, made in [wger runbook step 8](wger.md#8-api-key-per-person--1password)).
A rotated key goes into 1Password first (that step), then this step again.

**Laptop (`op` signed in, SSH agent)**

```bash
{
  printf 'WGER_API_KEY_JMACK=%s\n' "$(op read op://kdk-cluster/wger-api-jmack/credential)"
  printf 'WGER_API_KEY_RACH=%s\n'  "$(op read op://kdk-cluster/wger-api-rach/credential)"
  printf 'SCALE_RELAY_NOISE_KEY=%s\n' "$(op read op://kdk-cluster/scale-relay-esphome/credential)"
} | ssh kdkadmin@10.1.30.21 'sudo install -m 600 -o root -g root /dev/stdin /opt/kdk-lab/scale-bridge/secrets.env'
# verify: prints names and lengths only
ssh kdkadmin@10.1.30.21 'sudo awk -F= "{print \$1, length(\$2)}" /opt/kdk-lab/scale-bridge/secrets.env; sudo stat -c "%U:%G %a" /opt/kdk-lab/scale-bridge/secrets.env'
#   WGER_API_KEY_JMACK <n> / WGER_API_KEY_RACH <n> / SCALE_RELAY_NOISE_KEY <k> / root:root 600
# compare each <n> with the 1Password copy (op read adds a newline, so subtract 1):
op read op://kdk-cluster/wger-api-jmack/credential | wc -c     # = <n> + 1
op read op://kdk-cluster/wger-api-rach/credential  | wc -c     # = <n> + 1
op read op://kdk-cluster/scale-relay-esphome/credential | wc -c   # = <k> + 1
```

Three lines, all lengths non-zero and equal to `wc -c` minus the newline. Do not check
for a fixed length.

`printf` is a shell builtin, so the keys are never in a process argument list. If
`op read` says the field does not exist, the item stores the key under `password`: use
`.../password` instead.

`SCALE_RELAY_NOISE_KEY` is the relay's API encryption key (1Password
`scale-relay-esphome`, field `credential`; [Relay](#relay-scale-relay)). It must be there
while `SCALE_BRIDGE_ESPHOME_PROXIES` names it (step 3): named but missing or empty is
`config_invalid` and the container restarts.

## 2a. Engine key into 1Password (owner, once)

The engine needs Lefu's 8-electrode secret. The one used is Lefu's published **demo** key
(owner decision 2026-11-14: personal, non-commercial use). It is in Lefu's public demo repo
`LefuHengqi/BluetoothKit-AndroidDemo`, file
`app/src/main/java/com/lefu/ppblutoothkit/SecretManager.kt`: the 8-electrode secret string
(the vault note `hume-metrics-reproduction-2026-11-14.md`, section "Running the Lefu
calculation library locally", names which one the prototype used). It is deliberately not
copied into this repo, the PR or any log.

**Laptop (`op` signed in, `jq`)**

```bash
read -rs LEFU_V        # paste the value from that file, Enter (nothing is echoed)
op item template get Password \
  | LEFU_V="$LEFU_V" jq '.title = "lefu-engine-secret" | (.fields[] | select(.id == "password") | .value) = env.LEFU_V' \
  | op item create --vault kdk-cluster -  >/dev/null
unset LEFU_V
op read op://kdk-cluster/lefu-engine-secret/password | wc -c      # value length + 1 (newline), not 1
```

The value goes from stdin into the item template; it is never a command argument. If the
item exists already (a re-run), use `op item edit lefu-engine-secret --vault kdk-cluster -`
with the same template, or delete it in the app and run the block again.

## 2b. Engine secret (`LEFU_SECRET_8`)

Same pattern as step 2: rewrite the whole `secrets.env` with a third line.

**Laptop (`op` signed in, SSH agent)**

```bash
{
  printf 'WGER_API_KEY_JMACK=%s\n' "$(op read op://kdk-cluster/wger-api-jmack/credential)"
  printf 'WGER_API_KEY_RACH=%s\n'  "$(op read op://kdk-cluster/wger-api-rach/credential)"
  printf 'SCALE_RELAY_NOISE_KEY=%s\n' "$(op read op://kdk-cluster/scale-relay-esphome/credential)"
  printf 'LEFU_SECRET_8=%s\n'      "$(op read op://kdk-cluster/lefu-engine-secret/password)"
} | ssh kdkadmin@10.1.30.21 'sudo install -m 600 -o root -g root /dev/stdin /opt/kdk-lab/scale-bridge/secrets.env'
ssh kdkadmin@10.1.30.21 'sudo awk -F= "{print \$1, length(\$2)}" /opt/kdk-lab/scale-bridge/secrets.env; sudo stat -c "%U:%G %a" /opt/kdk-lab/scale-bridge/secrets.env'
#   WGER_API_KEY_JMACK <n> / WGER_API_KEY_RACH <n> / SCALE_RELAY_NOISE_KEY <k> / LEFU_SECRET_8 <m> / root:root 600
op read op://kdk-cluster/lefu-engine-secret/password | wc -c     # = <m> + 1
```

Keep the relay line (step 2) inside the braces too. Without the `LEFU_SECRET_8` line the bridge logs
`engine_config` `enabled: false`, `reason: no_secret` at startup and runs on without the
engine.

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
SCALE_BRIDGE_PEOPLE='[{"name":"jmack","min_lb":<lo>,"max_lb":<hi>,"token_env":"WGER_API_KEY_JMACK","height_cm":<cm>,"sex":"male","birth_date":"<YYYY-MM-DD>"},{"name":"rach","min_lb":<lo>,"max_lb":<hi>,"token_env":"WGER_API_KEY_RACH","height_cm":<cm>,"sex":"female","birth_date":"<YYYY-MM-DD>"}]'
SCALE_ADDRESS=<address from the vault capture note>
SCALE_BRIDGE_ESPHOME_PROXIES=10.1.30.240=SCALE_RELAY_NOISE_KEY
```

- Bands: pounds, inclusive, a few lb either side of each person's recent weight, and
  **not overlapping** (a weight in two bands is written nowhere). Kilograms still work
  (`min_kg`/`max_kg` instead): one pair per person, both ends in the same unit; both
  pairs, or a mixed pair, is `config_invalid`. A file written with kg bands before
  0.1.1 keeps working unchanged.
- **Engine profile (0.2.0, optional per person):** `height_cm` (a number; the engine uses
  whole cm, truncated), `sex` (`"male"` / `"female"`) and either `birth_date`
  (`"YYYY-MM-DD"`, preferred: the age is worked out on each weigh-in's date) or `age` (whole
  years, if you would rather not store a birth date; it then has to be bumped by hand). All
  or nothing per person: a person without these keys is weighed as before, with no engine
  run (`engine_skipped` `no_profile`); half a profile, both `birth_date` and `age`, or a bad
  value is `config_invalid` naming the field (never the value). Health data, like the bands:
  this file only.
- `SCALE_ADDRESS`: the scale's address from the vault capture note. Empty = any device
  named `Body Pod 2` (fine for the dry-run; startup logs `config_address_filter_off`).
  **Required for live mode (step 6).** The bridge does not enforce it: live mode starts
  without it, logs the warning and writes any `Body Pod 2` it hears.
- `SCALE_BRIDGE_ESPHOME_PROXIES`: the relay, `10.1.30.240=SCALE_RELAY_NOISE_KEY` (format
  `host[=NOISE_ENV][,host2[=NOISE_ENV2]]`; the key itself is in `secrets.env`, step 2).
  Empty = the host's own adapter, which cannot reach the scale ([Relay](#relay-scale-relay)).

Save, then hand the file back to root:

```bash
ssh kdkadmin@10.1.30.21 'sudo chown root:root /opt/kdk-lab/scale-bridge/scale-bridge.env && sudo chmod 600 /opt/kdk-lab/scale-bridge/scale-bridge.env && sudo stat -c "%U:%G %a" /opt/kdk-lab/scale-bridge/scale-bridge.env'
#   root:root 600
```

## 4. Deploy (Komodo)

Merge the PR. The `gitops-sync` procedure (every 15 min) creates the stack and deploys
it; to go now: **komodo.kmkdp.com → Procedures → gitops-sync → Run**, or **Stacks →
scale-bridge → Deploy**. The deploy builds `kdk/scale-bridge:<tag in compose.yaml>` on the Pi. Since 0.2.0
the build also downloads the engine inputs (about 40 MB from `raw.githubusercontent.com` and
`repo1.maven.org`, every file sha256-checked, plus the Temurin JDK base image from Docker
Hub) and compiles/jlinks the engine: expect a few minutes the first time (laptop: 1 min
natively, 5 min 20 s for arm64 under qemu; the Pi natively is not measured yet), about
600 MiB of memory at the peak. Later deploys reuse the cached engine stage unless `engine/`
changes. Note the Pi's build time from the Komodo deploy log for the vault.

**Laptop (SSH agent)**

```bash
ssh kdkadmin@10.1.30.21 'sudo docker ps --filter name=scale-bridge --format "{{.Names}} {{.Status}}"'
#   scale-bridge Up ... (healthy)          (health starts after ~1 min)
ssh kdkadmin@10.1.30.21 'sudo docker logs scale-bridge 2>&1 | head -5'
#   {"event":"config","version":"0.3.1","dry_run":false,"write_heart_rate":true,"write_engine":true,"write_derived":true,"people":["jmack","rach"],...}
#   {"event":"token_check","person":"jmack","result":"ok","status":200}   (and rach)
#   {"event":"engine_config","enabled":true,"reason":"on","variant":"PPDeviceCalcuteTypeAlternate8_4","limb_order":"10432","df_x0":"20k","profiles":["jmack","rach"],"sdk_commit":"c1eb5ab4..."}
#   {"event":"capture","enabled":true,"dir":"/captures","keep_days":60}
#   {"event":"relay_start","proxy":"10.1.30.240","result":"connected",...}
#   {"event":"scanning","transport":"esphome","proxies":1,...}
```

`relay_start` `result: waiting` or `failed` = the relay is not answering
([Relay](#relay-scale-relay)); `transport: local` = `SCALE_BRIDGE_ESPHOME_PROXIES` is empty
(step 3).

`capture` `enabled: false` with a `capture_unavailable` error = the captures directory in
step 1 is missing or has the wrong owner.

`token_check` `missing` or `rejected` = step 2. `config_invalid` = step 3 (the error names
the field).

## 5. First dry-run weigh-in

How the first install was checked, before live mode. Since step 6 the same weigh-in logs
`wrote` instead of `would_write` (step 6); the `session` and `attribution` lines are the
same. To see `would_write` again, go back to dry-run ([Rollback](#rollback)).

The scale must be in radio range of the relay (`scale-relay`, [Relay](#relay-scale-relay)),
not of kdk-dkr-02. Bluetooth **off** on both phones: a phone with the Hume app takes the
scale first.

1. **Bathroom/scale:** step on, hold the handle until the display locks (~20 s), step off.
2. **Laptop:**

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs --since 5m scale-bridge 2>&1 | grep -E "\"event\":\"(session|attribution|would_write)\""'
```

Expected, in order:

| Line | Fields to check |
|---|---|
| `session` | `result: reading`, `ended_by: end_marker`, `settled: true`, `settled_state: "05"`, `impedance_frames: 2`, `extended_frames: 4`, `bad_frames: 0`, `end_marker: true`, `heart_rate: true` |
| `attribution` | `result: matched`, `person: <you>`, `impedance: true` |
| `would_write` | `weight_lb` = the display (lb, 0.1; `weight_kg` alongside), `impedance_values: 10`, `heart_rate: true`, 11 `categories` |
| `capture_written` | `file: <yyyymmddThhmmssZ>-<session>.jsonl`, `frames` = the `session` line's `frames` |

Then the other person. `ended_by: impedance_timeout` with `impedance_frames: 0` = handle
let go too early (a weight-only reading). `ended_by: end_marker_timeout` = a complete
reading, but the end marker (heart rate) did not come within 20 s; `end_marker: true` with
`heart_rate: false` = the scale sent the marker with no heart rate. The session now lasts
about 5 s longer than before 0.1.3 (it waits for the marker): stay on until the display
shows the result. The `would_write` line is the only place the
weight is logged.

## 5b. Attended first engine run

The first run on kdk-dkr-02 is the real test of the two untested parts: Unicorn 2 on a real
arm64 host, and the Pi's 16 KiB-page kernel. Stay at the laptop for it.

1. **Startup:** `engine_config` `enabled: true` (step 4). `reason: no_secret` = step 2b;
   `profiles` lists who has a profile (step 3).
2. **Weigh in** as in step 5 (one person with a profile), then:

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs --since 5m scale-bridge 2>&1 | grep -E "\"event\":\"(session|would_write|engine|engine_skipped|capture_written)\""'
```

| Line | Good | Bad and what it means |
|---|---|---|
| `engine` | `result: ok`, `error_type: PP_ERROR_TYPE_NONE`, `fields` > 100, `duration_s` a few seconds | `engine_error` + `PP_ERROR_TYPE_SECRET` = wrong key (2a/2b); `timeout`, `nonzero_exit`, `calc_exception`, `crashed` = the engine failed on the Pi: note the reason code, `exit_code`, `duration_s` |
| `engine_skipped` | absent | `no_profile` (step 3) or `df_incomplete` (the scale sent fewer than four `DF` frames; `extended_frames` in `session`) |
| `capture_written` | has `engine_file: <name>.engine.json` | missing = no result to write (see the `engine` line) |

3. **The result file exists** (names and sizes only; the content is health data):

```bash
ssh kdkadmin@10.1.30.21 'sudo ls -l /opt/kdk-lab/scale-bridge/captures | tail -4'
#   -rw-r----- 1 nobody root ... <stamp>-<session>.jsonl
#   -rw-r----- 1 nobody root ... <stamp>-<session>.engine.json
ssh kdkadmin@10.1.30.21 'sudo tail -n1 /opt/kdk-lab/scale-bridge/captures/<stamp>-<session>.jsonl | python3 -c "import json,sys; f=json.load(sys.stdin); print(f[\"engine\"], f[\"engine_result\"])"'
#   ok True
```

4. **Timings** (no values): compare with the prototype (x86_64: ~1.3 s cold per weigh-in;
   arm64 under qemu: ~1.1 s init + ~0.85 s per calculation; Pi expected a few seconds):

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs --since 5m scale-bridge 2>&1 | grep "\"event\":\"engine\"" | python3 -c "import json,sys; [print({k: json.loads(l).get(k) for k in (\"result\",\"duration_s\",\"init_ms\",\"calc_ms\")}) for l in sys.stdin]"'
ssh kdkadmin@10.1.30.21 'sudo docker stats --no-stream --format "{{.Name}} {{.MemUsage}}" scale-bridge'
```

   `duration_s` is the whole child run (JVM start + init + calculation); `init_ms` +
   `calc_ms` is the engine's own share. Note all three and the memory in the vault note, as
   the Pi's first measurements. Memory well under the 256 MiB limit is expected (~80 MiB peak
   for the bridge + one engine run on amd64).
5. **Values:** to compare with the Hume app, copy the `.engine.json` to the laptop the way
   step 7 copies captures (vault only, never a git checkout) and read it there.
6. **If it fails repeatedly** (`timeout`, `crashed`, or the container restarts): set
   `SCALE_BRIDGE_ENGINE: "off"` (rollback below). Weigh-ins, captures and the wger write are
   unaffected either way.

Since 0.3.0 an `ok` result is also written to wger: [step 6b](#6b-engine-results-and-heart-rate-to-wger-030).

## 6. Live writes (dry-run off)

**Status: live** since the go-live PR (`SCALE_BRIDGE_DRY_RUN: "false"` in
`stacks/scale-bridge/compose.yaml`, 2026-11-16). Every matched weigh-in is written to that
person's wger. Since 0.3.0 the heart rate and the engine results are written too
([6b](#6b-engine-results-and-heart-rate-to-wger-030)); the checks below are the 0.2.x go-live.

**Before merging** (done 2026-11-16), on the host (step 3): `SCALE_ADDRESS` pinned and the
real bands in place, not overlapping. The code does not refuse live mode without them: no
`SCALE_ADDRESS` = a warning and any `Body Pod 2` is written to someone's wger; overlapping
bands = a warning and such a weight is written nowhere; a missing key = `token_check`
`missing` and each of that person's readings logs `write_skipped`. So check the startup
lines:

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs --since 5m scale-bridge 2>&1 | grep -E "\"event\":\"(config|config_address_filter_off|config_bands_overlap|token_check)\""'
#   config ... "address_filter":true, token_check result ok per person
#   and NO config_address_filter_off / config_bands_overlap line
```

**Merge = deploy.** The stack has `webhook_enabled` and `run_build` in
`komodo/resources.toml`: the merge rebuilds the image (same tag, cached) and recreates the
container with the new environment. A weigh-in from then on is written.

**After the merge, in this order:**

1. **Startup** (as step 4): `config` `dry_run: false`, `address_filter: true`;
   `token_check` `ok` for both; `scanning` `dry_run: false`, `transport: esphome`; no
   `config_address_filter_off` / `config_bands_overlap`.
2. **One weigh-in** as in step 5 (barefoot, hold the handle until the display shows the
   result, Bluetooth off on both phones), then:

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs --since 10m scale-bridge 2>&1 | grep -E "\"event\":\"(session|attribution|would_write|wrote|write_failed|write_crashed|write_gave_up|write_skipped)\""'
```

| Line | Expected |
|---|---|
| `session` | `result: reading`, as in step 5 |
| `attribution` | `result: matched`, `person: <you>` |
| `would_write` | **absent** (present = still dry-run: the deploy did not pick up the change) |
| `wrote` | `person`, `external_id`, `created: 11`, `exists: 0`, `categories_created: 10` on that person's first live write; later `categories_created: 0`. A weight-only reading (handle let go early) = `created: 1` and no impedance categories |

   The write runs in the background after the `session` line, normally within seconds.
3. **wger web (`https://wger.kmkdp.com`, as that person) → Measurements:**
   - **Body weight**: one new entry at the weigh-in time (kg). If that person had never
     logged a weight, this first one also creates the official Body weight category, in the
     profile's unit.
   - **Ten new custom categories**, unit `ohm`, one entry each at the same time:
     `Impedance 50 kHz right arm`, `Impedance 50 kHz left arm`, `Impedance 50 kHz trunk`,
     `Impedance 50 kHz right leg`, `Impedance 50 kHz left leg`, and the same five with
     `250 kHz`.
   - **Nothing else** (in 0.2.x): no `Heart rate (scale)` category, no engine categories.
     From 0.3.0: [6b](#6b-engine-results-and-heart-rate-to-wger-030).
4. **The other person**, the same way.

**Replays and duplicates.** The scale re-sends the same reading when it reconnects; the
60 s cooldown after a reading ignores it while the container runs. After a restart, or on a
retry, a reading whose weight (to 5 g) already has a Body weight entry within
`SCALE_BRIDGE_REPLAY_WINDOW_S` (600 s) is the same weigh-in: `wrote` with `created: 0` and
`exists` counting the rows already there (11 for a full reading; after a weight-only one,
the replay adds the missing impedance: `exists: 1`, `created: 10`). Each entry also carries
an `external_id`; wger rejects a repeat of it, counted as `exists`. Cost: a genuine second
weigh-in by the same person at exactly the same weight within 10 min is not written.

**Failures.** `write_failed` (warning: method, path and HTTP status or error type) and
`write_crashed` (an unexpected exception, type only) retry after 30, 60, 120, 240 and
480 s: 6 tries, 15.5 min. `write_gave_up` after the sixth = that reading is not in wger:
enter it in the wger app. Retries are in memory only: a redeploy or restart during the waits
drops them without a `write_gave_up` line.

**Undo a wrong write** (wrong person, or a test): in that person's wger web →
Measurements, delete the Body weight entry at that time and the ten impedance entries at the
same time. To remove everything the bridge made for a person, delete the ten
`Impedance …` categories (that deletes their entries) and the bridge's Body weight entries;
**never delete the Body weight category itself** (it holds the weights logged by hand).

**Back to dry-run:** revert the go-live PR and merge the revert (merge = deploy); startup
then logs `dry_run: true` and weigh-ins log `would_write` again. Entries already written
stay in wger (undo above).

## 6b. Engine results and heart rate to wger (0.3.0)

**Status:** compose `SCALE_BRIDGE_WRITE_ENGINE: "true"` and `SCALE_BRIDGE_WRITE_HEART_RATE:
"true"` since the 0.3.0 PR. No host step. Mapping and rules: [service
page](../services/scale-bridge.md#engine-results-in-wger).

**After the merge (merge = deploy):** startup `config` shows `"version":"0.3.0"`,
`"write_heart_rate":true`, `"write_engine":true`. Then one weigh-in per person (handle held to
the end), and:

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs --since 10m scale-bridge 2>&1 | grep -E "\"event\":\"(engine|engine_skipped|wrote|write_failed|write_gave_up)\""'
```

| Line | Expected on that person's first 0.3.0 weigh-in |
|---|---|
| `engine` | `result: ok`, `error_type: PP_ERROR_TYPE_NONE`, `duration_s` about 1.2 |
| `wrote` | `created: 12` (11 + heart rate; 11 if the scale sent no heart rate), `categories_created: 1` (the heart-rate category; 0 later), `engine_result: ok`, `engine_created: 16`, `engine_categories_created: 16` (0 later), `engine_skipped: 0`, `engine_rejected: 0` |

In wger (as that person → Measurements): one new custom category `Heart rate (scale)` (bpm)
and 16 new custom categories `Body fat (scale)`, `Skeletal muscle (scale)`, `Body water
(scale)`, `Visceral fat (scale)`, `BMR (scale)`, `Bone mineral (scale)`, `ECW (scale)`, `ICW
(scale)`, `Lean mass (scale)`, `Body cell mass (scale)`, `BMI (scale)`, `Lean right arm
(scale)`, `Lean left arm (scale)`, `Lean trunk (scale)`, `Lean right leg (scale)`, `Lean left
leg (scale)`, each with one entry at the weigh-in's time. The mass ones carry the unit of that
person's wger profile (kg or lb). wger's own `Body fat` / `Heart rate` categories (Apple Health)
get nothing.

`engine_result` other than `ok` (`no_profile`, `engine_off`, `not_run`, `no_result`, or the
engine's reason code) = no engine rows for that weigh-in; the weight and impedance are in.

**Switch off:** a one-line PR, `SCALE_BRIDGE_WRITE_ENGINE: "false"` (or
`SCALE_BRIDGE_WRITE_HEART_RATE: "false"`) in `stacks/scale-bridge/compose.yaml`; merge =
deploy. The engine keeps running and writing `.engine.json`; nothing else changes.

**Undo:** in that person's wger web → Measurements, delete the `… (scale)` categories (that
deletes their entries). Never delete `Body weight` or the categories without `(scale)` that
Apple Health feeds.

## 6c. Derived values to wger (0.3.1)

**Status:** compose `SCALE_BRIDGE_WRITE_DERIVED: "true"` since the 0.3.1 PR. No host step.
Formulas, constants and evidence: [service page](../services/scale-bridge.md#derived-values-in-wger).
Only has effect with `SCALE_BRIDGE_WRITE_ENGINE: "true"` and an `ok` engine result.

**After the merge (merge = deploy):** startup `config` shows `"version":"0.3.1"`,
`"write_derived":true`. Then one weigh-in, and the same log command as 6b:

| Line | Expected on that person's first 0.3.1 weigh-in |
|---|---|
| `wrote` | as 6b (`engine_created: 16` on a new weigh-in), plus `derived_created: 5`, `derived_categories_created: 5` (0 later), `derived_skipped: 0`, `derived_skipped_sex: 0`, `derived_rejected: 0` for a male profile; for any other profile `derived_created: 1`, `derived_categories_created: 1`, `derived_skipped_sex: 4` |

In wger (as that person → Measurements), the first write creates the custom categories
`Mineral (scale est.)`, `Android fat (scale est.)`, `Gynoid fat (scale est.)` (these three in
the profile's weight unit, kg or lb), `A/G ratio (scale est.)` and `Torso fat (scale est.)` (`%`),
each with one entry at the weigh-in's time; for a profile that is not male only `Mineral (scale
est.)`. A retry or replay of a weigh-in written by 0.3.0 adds only these rows.

**Switch off:** a one-line PR, `SCALE_BRIDGE_WRITE_DERIVED: "false"` in
`stacks/scale-bridge/compose.yaml`; merge = deploy. The 16 engine rows are unchanged.

**Undo:** in that person's wger web → Measurements, delete the five `… (scale est.)`
categories (that deletes their entries).

**Change a constant** (after more paired weigh-ins): a one-line PR to `ANDROID_PER_TRUNK_FAT`,
`GYNOID_PER_LEG_FAT` or `TORSO_SHARE_OF_WEIGHT` in `stacks/scale-bridge/scale_bridge/wger.py`
plus the version bump (`pyproject.toml`, `__init__.py`, compose image tag, `uv lock`) and the
service-page table. Entries already written keep the old constant (their `extra_data.formula`
names it); only weigh-ins after the deploy use the new one.

## 7. Raw capture files

One file per weigh-in session in `/opt/kdk-lab/scale-bridge/captures` (what is in them and
why: [service page](../services/scale-bridge.md#raw-capture-files)). They are health data:
copy them only to the laptop (into the vault, never into a git checkout) or to the emulator
host for a replay, and delete copies when the test is done.

**Laptop (SSH agent)**

```bash
# list (newest last): names carry the UTC start time and the session id
ssh kdkadmin@10.1.30.21 'sudo ls -l /opt/kdk-lab/scale-bridge/captures'
# frame count of one file without printing it (header + frames + footer = lines)
ssh kdkadmin@10.1.30.21 'sudo wc -l /opt/kdk-lab/scale-bridge/captures/<file>.jsonl'
# copy one to the laptop (root-owned on the host: sudo cat over SSH, never scp as root)
D="$HOME/Documents/kodiak-codex/1. Projects/exercise/scale-sniff/captures-bridge"
(umask 077; mkdir -p "$D" && ssh kdkadmin@10.1.30.21 'sudo cat /opt/kdk-lab/scale-bridge/captures/<file>.jsonl' > "$D/<file>.jsonl")
# copy all of them
(umask 077; mkdir -p "$D" && ssh kdkadmin@10.1.30.21 'sudo tar -C /opt/kdk-lab/scale-bridge/captures -cf - .' | tar -C "$D" -xf -)
```

Replay: the emulator reads the records with `"kind": "frame"` (`t`, `hex`) and ignores the
header and footer, exactly as for the sniff-script captures.

Delete (they also expire by themselves after `SCALE_BRIDGE_CAPTURE_KEEP_DAYS`, 60 days):

```bash
ssh kdkadmin@10.1.30.21 'sudo rm /opt/kdk-lab/scale-bridge/captures/<file>.jsonl'       # one
ssh kdkadmin@10.1.30.21 'sudo find /opt/kdk-lab/scale-bridge/captures -name "*.jsonl" -delete'   # all
ssh kdkadmin@10.1.30.21 'sudo find /opt/kdk-lab/scale-bridge/captures -name "*.engine.json" -delete'   # all engine results
```

Capturing off: set `SCALE_BRIDGE_CAPTURE_DIR: ""` in `stacks/scale-bridge/compose.yaml` (a
PR), deploy, then delete the files.

**Heart rate to wger:** on since 0.3.0 (the byte matched the vendor app on 6 of 6 paired
weigh-ins): [step 6b](#6b-engine-results-and-heart-rate-to-wger-030).

## Relay (`scale-relay`)

The bridge hears the scale only through the ESPHome Bluetooth proxy `scale-relay`
(Waveshare ESP32-S3-ETH, wired Ethernet, `10.1.30.240`; [service page](../services/scale-bridge.md#relay-scale-relay)).
Config: `stacks/scale-bridge/esphome/scale-relay.yaml`. Secrets: 1Password
`op://kdk-cluster/scale-relay-esphome` (`credential` = API encryption key, `ota_password`).

**If it is off** (no power, unplugged, flashing): the bridge hears nothing, no `session`
lines, and weigh-ins are **not recorded** until it is back (enter a missed one in the wger
app). Nothing else breaks; the bridge keeps trying to reach it.

### Build and flash an update

Over the network (OTA). USB only for the first flash or a recovery (board does not come
back on the network).

**Laptop (`op` signed in, ESPHome in `~/.local/share/esphome-venv`, a checkout of this repo)**

```bash
cd stacks/scale-bridge/esphome
ESPHOME=~/.local/share/esphome-venv/bin/esphome
# secrets.yaml from 1Password: mode 600 from the start, values never printed (git-ignored)
(umask 077; {
  printf 'scale_relay_api_key: |-\n  %s\n'      "$(op read op://kdk-cluster/scale-relay-esphome/credential)"
  printf 'scale_relay_ota_password: |-\n  %s\n' "$(op read op://kdk-cluster/scale-relay-esphome/ota_password)"
} > secrets.yaml)
stat -c '%a %n' secrets.yaml                        # 600 secrets.yaml
"$ESPHOME" config scale-relay.yaml >/dev/null && echo valid     # valid (checks the key format too)
"$ESPHOME" run scale-relay.yaml --device 10.1.30.240 --no-logs # build + OTA; the board reboots
rm secrets.yaml
```

A wrong OTA password fails the upload and leaves the old firmware running. The block scalar
(`|-`) keeps any character in a value safe for YAML.

First flash or recovery: same `secrets.yaml`, board on the laptop's USB port, then
`"$ESPHOME" run scale-relay.yaml --device /dev/ttyACM0` (the port name may differ: `ls
/dev/ttyACM* /dev/ttyUSB*`). A changed API key needs the same key in
`secrets.env` (step 2) and a bridge redeploy.

### Check it is working

```bash
ssh kdkadmin@10.1.30.21 'sudo docker logs scale-bridge 2>&1 | grep -E "\"event\":\"(relay_start|scanning)\"" | tail -2'
#   {"event":"relay_start","proxy":"10.1.30.240","result":"connected",...}
#   {"event":"scanning","transport":"esphome","proxies":1,...}
```

`relay_start` is logged at bridge startup only. After an OTA the bridge should reconnect by
itself (bleak-esphome retries in the background; not yet watched after an OTA): the next
weigh-in's `session` line is the proof, or redeploy (Komodo → Stacks → scale-bridge →
Deploy) and read `relay_start` again. The board's own log: `"$ESPHOME" logs scale-relay.yaml
--device 10.1.30.240` (needs `secrets.yaml` as above; delete it afterwards). In the bridge's
`session` lines the RSSI is the relay's view of the scale (about -75 to -79 dBm in the
temporary spot).

## Troubleshooting

- `fatal` `ConnectionResetError` `[Errno 104]` right after the `config` line: the
  container uid has no passwd entry on kdk-dkr-02, and dbus-daemon resets the system-bus
  connection (0.1.0 ran as 65532 and hit this on the first deploy). 0.1.1 and later run as
  `65534:65534` (`nobody:nogroup`); check with `getent passwd 65534` on the host.
- Ad-hoc `docker run --env-file /opt/kdk-lab/scale-bridge/scale-bridge.env ...` test
  containers: `docker run` keeps the single quotes around the `SCALE_BRIDGE_PEOPLE` JSON
  (compose `env_file` strips them), so the value fails to parse. Use a root-600 copy with the
  quotes removed, or test through compose. The Komodo deploy is not affected.
- Other symptoms: [service troubleshooting](../services/scale-bridge.md#troubleshooting).

## Rollback

- **Stop it:** Komodo → Stacks → scale-bridge → Destroy. To keep it gone, revert the PR
  (the `[[stack]]` entry) so the next sync does not redeploy it.
- **Back to dry-run only:** revert the go-live PR (step 6), merge the revert (that
  deploys it). Entries already written stay in wger: [step 6 "Undo"](#6-live-writes-dry-run-off).
- **Relay out of use:** `SCALE_BRIDGE_ESPHOME_PROXIES=` (empty) in `scale-bridge.env`, redeploy:
  the bridge falls back to the host adapter, which is out of range of the scale today.
  Firmware: flash the previous `scale-relay.yaml` from git history ([Relay](#relay-scale-relay)).
- **Engine results out of wger (0.3.0), keep everything else:** a one-line PR,
  `SCALE_BRIDGE_WRITE_ENGINE: "false"`, merge = deploy; delete the `… (scale)` categories if
  wanted ([6b](#6b-engine-results-and-heart-rate-to-wger-030)). Heart rate the same way with
  `SCALE_BRIDGE_WRITE_HEART_RATE: "false"`.
- **Derived values out of wger (0.3.1), keep everything else:** a one-line PR,
  `SCALE_BRIDGE_WRITE_DERIVED: "false"`, merge = deploy; delete the five `… (scale est.)`
  categories if wanted ([6c](#6c-derived-values-to-wger-031)).
- **Back to 0.3.0:** revert the 0.3.1 PR, merge = deploy. Rows already written stay in wger
  (6c "Undo").
- **Back to 0.2.0:** revert the 0.3.0 PR, merge = deploy. Rows already written stay in wger
  (6b "Undo").
- **Engine off (0.2.0), keep everything else:** a one-line PR,
  `SCALE_BRIDGE_ENGINE: "off"` in `stacks/scale-bridge/compose.yaml`, deploy. Or remove the
  `LEFU_SECRET_8` line from `secrets.env` (step 2 without it) and redeploy: same effect.
- **Back to 0.1.3 (no engine):** revert the 0.2.0 PR, deploy. The profile keys in
  `scale-bridge.env` can stay (0.1.3 ignores keys it does not know). The `.engine.json`
  files stay: 0.1.3's pruning only matches `.jsonl`, so delete them as in step 7. The
  1Password item `lefu-engine-secret` and the `LEFU_SECRET_8` line can stay or go.
- **Back to 0.1.2 (no end-marker wait, no captures):** revert the 0.1.3 PR, deploy. The
  captures directory and its files stay; delete them as in step 7 if not wanted.
- **Left behind:** `/opt/kdk-lab/scale-bridge/` including `captures/` (health data; remove with
  `ssh kdkadmin@10.1.30.21 'sudo rm -r /opt/kdk-lab/scale-bridge'`), the image
  (`sudo docker image rm kdk/scale-bridge:0.3.1`, plus `:0.3.0` / `:0.2.0` / `:0.1.3` / `:0.1.2` / `:0.1.1` / `:0.1.0` if still there), and in wger the written entries and the
  ten impedance categories, `Heart rate (scale)`, the 16 engine `… (scale)` and the five `… (scale est.)` categories (delete per person in the web UI; deleting a category deletes
  its entries). The wger keys stay: other tools use them.
