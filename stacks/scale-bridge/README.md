# scale-bridge

Body Pod 2 (Lefu FFF0) BLE scale → wger body weight + ten raw impedance values, per
person by weight band. kdk-dkr-02, Komodo stack `scale-bridge`, dry-run by default.
Service page: [docs/services/scale-bridge.md](../../docs/services/scale-bridge.md).
Owner steps: [docs/runbooks/scale-bridge.md](../../docs/runbooks/scale-bridge.md).

| File | What |
|---|---|
| `scale_bridge/protocol.py` | frame codec: XOR check, `CF` weight, `CF` end-of-measurement marker (heart rate), `BE` impedance, `DF` raw, profile frame |
| `scale_bridge/session.py` | one connection: subscribe, profile write, settle rules, wait for the end marker, end conditions; records every notification |
| `scale_bridge/capture.py` | raw capture JSONL per session (`/captures`, 0640, pruned), for replaying to the vendor app |
| `scale_bridge/ble.py` | scanner, discovery filter, connect (bleak-retry-connector), optional ESPHome relay |
| `scale_bridge/attribution.py` | weight bands from `SCALE_BRIDGE_PEOPLE` |
| `scale_bridge/wger.py` | wger 2.7 client: official body-weight category, custom impedance categories, `external_id` idempotence |
| `scale_bridge/app.py` | reading → person → dry-run line or background write with retries |
| `scale-bridge.env.example`, `secrets.env.example` | shapes of the two host files (made-up values / names only) |
| `tests/` | pytest; `fixtures/bodypod2-session-synthetic.jsonl` is a SYNTHETIC session (real structure and timing, rescaled 70.00 kg, replaced impedance/DF/address, made-up heart-rate byte in the end marker, recomputed checksums) |

Never commit a real capture (from the sniff script or `/captures`): they hold body weight,
impedance and heart rate.

```bash
cd stacks/scale-bridge
uv run --frozen pytest -q          # CI: .github/workflows/scale-bridge.yml
```

Env (compose `environment` unless noted; defaults in `scale_bridge/config.py`):
`SCALE_BRIDGE_DRY_RUN` (true), `WGER_URL`, `SCALE_NAME`, `SCALE_PROFILE_*` (placeholders,
not anyone's data), `SCALE_SESSION_TIMEOUT_S` (90), `SCALE_IMPEDANCE_WAIT_S` (5),
`SCALE_DF_GRACE_S` (1), `SCALE_END_MARKER_WAIT_S` (20), `SCALE_CONNECT_ATTEMPTS` (3), `SCALE_CONNECT_TIMEOUT_S` (30),
`SCALE_COOLDOWN_S` (60), `SCALE_BRIDGE_WRITE_RETRIES` (6: waits 30 s doubling, 15.5 min),
`SCALE_BRIDGE_REPLAY_WINDOW_S` (600: same weight within this = the same weigh-in), `SCALE_BRIDGE_LOG_LEVEL` (INFO),
`SCALE_BRIDGE_WRITE_HEART_RATE` (false), `SCALE_BRIDGE_CAPTURE_DIR` (empty = off; compose `/captures`),
`SCALE_BRIDGE_CAPTURE_KEEP_DAYS` (60; 0 = forever);
host env files: `SCALE_BRIDGE_PEOPLE`, `SCALE_ADDRESS`, `SCALE_BRIDGE_ESPHOME_PROXIES`, the
`WGER_API_KEY_*` named by each person's `token_env`, proxy noise keys.
