# scale-bridge

Body Pod 2 (Lefu FFF0) BLE scale → wger body weight (+ ten raw impedance values until 0.3.1), per
person by weight band, plus Lefu's own 8-electrode body-composition engine run once per
weigh-in (results to the captures directory and, since 0.3.0, to 16 custom
`… (scale)` categories in wger, and since 0.3.1 five derived `… (scale est.)` values).
kdk-dkr-02, Komodo stack
`scale-bridge`, live writes since 2026-11-16 (compose `SCALE_BRIDGE_DRY_RUN: "false"`;
the code default is dry-run).
Service page: [docs/services/scale-bridge.md](../../docs/services/scale-bridge.md).
Owner steps: [docs/runbooks/scale-bridge.md](../../docs/runbooks/scale-bridge.md).

| File | What |
|---|---|
| `scale_bridge/protocol.py` | frame codec: XOR check, `CF` weight, `CF` end-of-measurement marker (heart rate), `BE` impedance, `DF` raw, profile frame |
| `scale_bridge/session.py` | one connection: subscribe, profile write, settle rules, wait for the end marker, end conditions; records every notification |
| `scale_bridge/capture.py` | raw capture JSONL per session (`/captures`, 0640, pruned), for replaying to the vendor app |
| `scale_bridge/ble.py` | scanner, discovery filter, connect (bleak-retry-connector), optional ESPHome relay |
| `scale_bridge/attribution.py` | weight bands from `SCALE_BRIDGE_PEOPLE` |
| `scale_bridge/wger.py` | wger 2.7 client: official body-weight category, custom impedance / heart-rate / engine categories (`ENGINE_METRICS`: category, getter, unit; `DERIVED_METRICS`: category, unit, formula over the engine's `out`), kg/lb for mass metrics, `external_id` idempotence |
| `scale_bridge/app.py` | reading → person → dry-run line or background write with retries; `Bridge.compute` runs the engine |
| `scale_bridge/engine.py` | engine child process: DF decode, limb order, profile → case on stdin, secret in the child env only, timeout, JSON parse, reason codes |
| `engine/` | the engine itself (Java): `deps.lock` + `sdk.lock` (sha256 pins), `fetch.sh` (build-stage download + check), `patcher/` (ASM rewrite of the vendor `native` methods), `src/` (unidbg bridge, CLI `kdk.lefu.Main`), `lefu-engine.sh` (JVM wrapper), `smoke.py` (in-image check, dummy secret) |
| `scale-bridge.env.example`, `secrets.env.example` | shapes of the two host files (made-up values / names only) |
| `esphome/` | `scale-relay.yaml`: ESPHome config of the Bluetooth relay board the bridge hears the scale through (10.1.30.240); `secrets.yaml.example` (key names only; the real `secrets.yaml` is git-ignored). Build and flash: runbook "Relay" |
| `tests/` | pytest; `fixtures/bodypod2-session-synthetic.jsonl` is a SYNTHETIC session (real structure and timing, rescaled 70.00 kg, replaced impedance/DF/address, made-up heart-rate byte in the end marker, recomputed checksums) |

Never commit a real capture (from the sniff script or `/captures`): they hold body weight,
impedance and heart rate.

```bash
cd stacks/scale-bridge
uv run --frozen pytest -q          # CI: .github/workflows/scale-bridge.yml
```

Env (compose `environment` unless noted; defaults in `scale_bridge/config.py`):
`SCALE_BRIDGE_DRY_RUN` (true; compose `false` = live), `WGER_URL`, `SCALE_NAME`, `SCALE_PROFILE_*` (placeholders,
not anyone's data), `SCALE_SESSION_TIMEOUT_S` (90), `SCALE_IMPEDANCE_WAIT_S` (5),
`SCALE_DF_GRACE_S` (1), `SCALE_END_MARKER_WAIT_S` (20), `SCALE_CONNECT_ATTEMPTS` (3), `SCALE_CONNECT_TIMEOUT_S` (30),
`SCALE_COOLDOWN_S` (60), `SCALE_BRIDGE_WRITE_RETRIES` (6: waits 30 s doubling, 15.5 min),
`SCALE_BRIDGE_REPLAY_WINDOW_S` (600: same weight within this = the same weigh-in), `SCALE_BRIDGE_LOG_LEVEL` (INFO),
`SCALE_BRIDGE_WRITE_IMPEDANCE` (true; compose `false` since 0.3.1: no `Impedance …` categories or rows, the values stay in the captures only), `SCALE_BRIDGE_WRITE_HEART_RATE` (false; compose `true`), `SCALE_BRIDGE_WRITE_ENGINE` (false; compose `true`:
engine results to wger), `SCALE_BRIDGE_WRITE_DERIVED` (false; compose `true` since 0.3.1: the five
derived `… (scale est.)` values, only with `SCALE_BRIDGE_WRITE_ENGINE`), `SCALE_BRIDGE_CAPTURE_DIR` (empty = off; compose `/captures`),
`SCALE_BRIDGE_CAPTURE_KEEP_DAYS` (60; 0 = forever), `SCALE_BRIDGE_ENGINE` (on; off = engine
never runs), `SCALE_BRIDGE_ENGINE_TIMEOUT_S` (60), `LEFU_VARIANT` (8_4), `LEFU_LIMB_ORDER`
(10432), `LEFU_DF_X0` (20k) — the last three are the known-good configuration, do not change
unless testing; `SCALE_BRIDGE_ENGINE_CMD` (tests only);
host env files: `SCALE_BRIDGE_PEOPLE` (bands + optional engine profile), `SCALE_ADDRESS`,
`SCALE_BRIDGE_ESPHOME_PROXIES` (`10.1.30.240=SCALE_RELAY_NOISE_KEY`), the `WGER_API_KEY_*` named by each person's `token_env`,
the proxy noise key `SCALE_RELAY_NOISE_KEY`, `LEFU_SECRET_8` (engine; optional).

## Engine

Lefu's 8-electrode calculation library (`ppcalculatekit` 4.6.28 + `ppbasiccalculatekit`
1.1.1, MIT) built into the same image, called as `lefu-engine --case -` once per weigh-in of
a matched person who has a profile, when all four `DF` frames arrived. The vendor's
Java/Kotlin wrapper runs unchanged on a jlink'd Temurin 21 JRE; only its `native` methods are
rewritten at build time (ASM) to call a bridge that runs the vendor's **arm64** `.so` inside
unidbg (an emulated arm64 Android process, Unicorn 2 CPU backend). No Android, no KVM, no
capability, no device, no network. Details and the acceptance results: vault
`1. Projects/exercise/hume-metrics-reproduction-2026-11-14.md`, section "Engine as a
container sidecar".

- **Secret:** `LEFU_SECRET_8`, read by the bridge from `secrets.env` and put into the child's
  environment only (with nothing else from the bridge's env but the `LEFU_*` paths, `PATH`,
  `TZ`). Never in the repo, an image layer, a build arg or a log line. Missing = the engine is
  off (one `engine_config` warning), everything else unchanged.
- **Build inputs** (Dockerfile stage `engine`): `engine/sdk.lock` (Lefu's public
  `LefuHengqi/PPBaseKit-Android` at a pinned commit, three `.aar` files) and
  `engine/deps.lock` (21 Maven Central jars, the complete set, no Maven resolution), every
  file sha256-checked. Hosts: `raw.githubusercontent.com`, `repo1.maven.org` (plus the
  registries for the two base images). Nothing vendor-owned is committed.
- **Run time:** `/tmp` (tmpfs, `noexec`) holds unidbg's guest folder (data only); native
  libraries load from `/opt/lefu` in the read-only image.
- **Output:** `<capture>.engine.json` next to the raw capture (engine output, inputs,
  provenance, timings); one `engine` log line (reason, duration, error type, field count).
  With `SCALE_BRIDGE_WRITE_ENGINE` (compose `true` since 0.3.0) an `ok` result also goes to
  wger, one custom category per metric (`ENGINE_METRICS` in `wger.py`); counts in the `wrote`
  line, never values. With `SCALE_BRIDGE_WRITE_DERIVED` (compose `true` since 0.3.1) also the
  five derived values (`DERIVED_METRICS`), counted as `derived_*`.
- **Check without the key:** `engine/smoke.py` inside the image (CI
  `.github/workflows/scale-bridge-engine.yml`): a dummy secret must come back as the
  library's `PP_ERROR_TYPE_SECRET`.

```bash
cd stacks/scale-bridge
docker build -t kdk/scale-bridge:dev .
LEFU_SECRET_8=$(printf '0%.0s' $(seq 64)) docker run --rm -i --read-only --tmpfs /tmp:rw,noexec,nosuid \
  --cap-drop ALL --security-opt no-new-privileges --network none --user 65534:65534 \
  -e LEFU_SECRET_8 --entrypoint python kdk/scale-bridge:dev -I - < engine/smoke.py   # smoke: pass
```

## Third-party software in the image (personal, non-commercial use)

The engine is used for the household's own weigh-ins only, not commercially (owner decision
2026-11-14). The image is built privately on kdk-dkr-02 and is not distributed, which is
what keeps the GPL parts below unproblematic.

| Component | Licence |
|---|---|
| Lefu SDK (`ppbasekit`, `ppcalculatekit`, `ppbasiccalculatekit`, incl. the native `.so`) | MIT |
| unidbg, unidbg-unicorn2 | Apache-2.0 |
| Unicorn 1 and 2 (CPU emulator, bundled natives), Keystone | GPL-2.0 |
| Capstone | BSD-3-Clause |
| JNA | Apache-2.0 / LGPL-2.1 |
| fastjson, commons-codec/-io/-collections4/-logging, kotlin-stdlib, JetBrains annotations, demumble | Apache-2.0 |
| apk-parser, native-lib-loader | BSD-2-Clause |
| slf4j | MIT |
| JSON-java (org.json) | Public Domain (JSON License text) |
| ASM (build stage only, not in the image) | BSD-3-Clause |
| Eclipse Temurin JRE (jlink) | GPL-2.0 with Classpath Exception |
| Python, bleak, httpx, … | as before (PSF, MIT, BSD) |

The secret is Lefu's published **demo** key, accepted because the engine's fake Android side
reports the demo package name `com.lefu.demo`; the owner accepted that for private use.
