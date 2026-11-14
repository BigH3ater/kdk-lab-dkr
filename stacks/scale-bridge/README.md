# scale-bridge

Body Pod 2 (Lefu FFF0) BLE scale → wger body weight + ten raw impedance values, per
person by weight band. kdk-dkr-02, Komodo stack `scale-bridge`, dry-run by default.
Service page: [docs/services/scale-bridge.md](../../docs/services/scale-bridge.md).
Owner steps: [docs/runbooks/scale-bridge.md](../../docs/runbooks/scale-bridge.md).

| File | What |
|---|---|
| `scale_bridge/protocol.py` | frame codec: XOR check, `CF` weight, `BE` impedance, `DF` raw, profile frame |
| `scale_bridge/session.py` | one connection: subscribe, profile write, settle rules (zero-weight trap), end conditions |
| `scale_bridge/ble.py` | scanner, discovery filter, connect (bleak-retry-connector), optional ESPHome relay |
| `scale_bridge/attribution.py` | weight bands from `SCALE_BRIDGE_PEOPLE` |
| `scale_bridge/wger.py` | wger 2.7 client: official body-weight category, custom impedance categories, `external_id` idempotence |
| `scale_bridge/app.py` | reading → person → dry-run line or background write with retries |
| `scale-bridge.env.example`, `secrets.env.example` | shapes of the two host files (made-up values / names only) |
| `tests/` | pytest; `fixtures/bodypod2-session-synthetic.jsonl` is a SYNTHETIC session (real structure and timing, rescaled 70.00 kg, replaced impedance/DF/address, recomputed checksums) |

Never commit a real capture: they hold body weight and impedance.

```bash
cd stacks/scale-bridge
uv run --frozen pytest -q          # CI: .github/workflows/scale-bridge.yml
```

Env (compose `environment` unless noted; defaults in `scale_bridge/config.py`):
`SCALE_BRIDGE_DRY_RUN` (true), `WGER_URL`, `SCALE_NAME`, `SCALE_PROFILE_*` (placeholders,
not anyone's data), `SCALE_SESSION_TIMEOUT_S` (90), `SCALE_IMPEDANCE_WAIT_S` (5),
`SCALE_DF_GRACE_S` (1), `SCALE_CONNECT_ATTEMPTS` (3), `SCALE_CONNECT_TIMEOUT_S` (30),
`SCALE_COOLDOWN_S` (60), `SCALE_BRIDGE_WRITE_RETRIES` (6), `SCALE_BRIDGE_LOG_LEVEL` (INFO);
host env files: `SCALE_BRIDGE_PEOPLE`, `SCALE_ADDRESS`, `SCALE_BRIDGE_ESPHOME_PROXIES`, the
`WGER_API_KEY_*` named by each person's `token_env`, proxy noise keys.
