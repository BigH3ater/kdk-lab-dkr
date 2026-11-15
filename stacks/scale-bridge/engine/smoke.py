"""Engine smoke test INSIDE the built image, on SYNTHETIC inputs with a DUMMY secret.

    docker run --rm -i <hardening flags> -e LEFU_SECRET_8=<64 zeros> --entrypoint python \
        kdk/scale-bridge:<tag> -I - < engine/smoke.py

Proves the whole chain loads (bridge -> child process -> JRE -> patched vendor jars ->
unidbg/Unicorn 2 -> the arm64 vendor library) without the real key: a wrong secret must
come back as the library's own PP_ERROR_TYPE_SECRET, cleanly, through the bridge's parser.
Prints reason codes, counts and timings only. Exit 0 = pass. CI: .github/workflows/scale-bridge.yml.
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

from scale_bridge.attribution import Profile
from scale_bridge.config import engine_from_env
from scale_bridge.engine import Engine, build_case
from scale_bridge.session import Reading


def df_frame(sub: int, x: int, values: list[int]) -> str:
    payload = b"".join(v.to_bytes(4, "little") for v in values)[: 12 if sub == 0 else 8].ljust(12, b"\0")
    body = bytes([0xDF, 0x02, sub, x]) + payload
    c = 0
    for b in body:
        c ^= b
    return (body + bytes([c])).hex()


def main() -> int:
    s = engine_from_env(dict(os.environ))
    eng = Engine(s)
    eng.startup(["synthetic"])
    if not eng.enabled:
        print(json.dumps({"smoke": "fail", "reason": eng.reason}))
        return 1
    vals = {0: [1000001, 1000002, 1000003, 1000004, 1000005], 1: [2000001, 2000002, 2000003, 2000004, 2000005]}
    df = tuple(df_frame(sub, x, vals[x][:3] if sub == 0 else vals[x][3:]) for sub in (0, 1) for x in (0, 1))
    reading = Reading(measured_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), weight_cg=7000,
                      z50_deci=None, z250_deci=None, df_raw=df)
    case = build_case(reading, Profile(height_cm=170.0, sex="male", age=30), s)
    assert case is not None and case["z20"] == [vals[0][i] for i in (1, 0, 4, 3, 2)], "DF decode/limb order"
    run = asyncio.run(eng.run(case))
    timing = (run.output or {}).get("timing")
    print(json.dumps({"result": run.result, "error_type": run.error_type, "fields": run.fields,
                      "exit_code": run.exit_code, "wall_s": run.duration_s, "timing": timing,
                      "engine_info": bool(eng.info.get("sdk_commit"))}))
    ok = run.result == "engine_error" and run.error_type == "PP_ERROR_TYPE_SECRET" and run.fields > 0
    print("smoke:", "pass" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
