#!/usr/bin/env python3
"""trivy-scan report: summarise this host's weekly Trivy pass into Loki.

Waits (via the GET-only socket proxy) for the `trivy-scan` container to exit,
reads /out/<n>.json + <n>.meta, and emits one logfmt line per image:

  ts=... image=traefik:v3.7 image_id=sha256:0123456789ab status=ok critical=1
  high=4 fixable_critical=1 fixable_high=3 top_cve=CVE-... top_pkg=... top_fixed=...

plus a summary line, to stdout and to Loki (job="trivy", host=NODE_NAME). The
Loki rule TrivyFixableCritical pages on fixable_critical > 0. Pushover is the
fallback when the push fails (PUSHOVER_MODE=fallback) or always (=always).
Exit 0 = delivered, 1 = the scan itself failed or scanned nothing, 2 = Loki
push failed. Stdlib only; `summarise()` is importable for tests.
"""
from __future__ import annotations

import datetime as dt, glob, json, os, re, sys, time, urllib.parse, urllib.request

SEV_ORDER = {"CRITICAL": 0, "HIGH": 1}


def q(v) -> str:
    s = "" if v is None else str(v)
    if s and re.fullmatch(r"[A-Za-z0-9_.:/@+,~*-]+", s):
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def summarise(report: dict) -> dict:
    """Counts + the most urgent finding from one `trivy image --format json` report."""
    vulns, seen = [], set()
    for res in report.get("Results") or []:
        for v in res.get("Vulnerabilities") or []:
            key = (v.get("VulnerabilityID"), v.get("PkgName"), v.get("InstalledVersion"))
            if key in seen:          # the same CVE in one package listed by two targets
                continue
            seen.add(key)
            vulns.append(v)
    fixable = lambda v: bool(v.get("FixedVersion"))
    crit = [v for v in vulns if v.get("Severity") == "CRITICAL"]
    high = [v for v in vulns if v.get("Severity") == "HIGH"]
    # Most urgent: fixable critical > critical > fixable high > high.
    ranked = sorted(vulns, key=lambda v: (SEV_ORDER.get(v.get("Severity"), 9), not fixable(v),
                                          v.get("VulnerabilityID", "")))
    top = ranked[0] if ranked else {}
    return {"critical": len(crit), "high": len(high),
            "fixable_critical": sum(map(fixable, crit)), "fixable_high": sum(map(fixable, high)),
            "top_cve": top.get("VulnerabilityID", "-"), "top_pkg": top.get("PkgName", "-"),
            "top_fixed": top.get("FixedVersion") or "-"}


def api(path: str) -> dict:
    with urllib.request.urlopen(os.environ.get("DOCKER_API", "http://trivy-scan-proxy:2375") + path,
                                timeout=15) as r:
        return json.loads(r.read())


def wait_for_scan(max_s: int) -> int:
    """Exit code of the trivy-scan container once it has stopped (-1 = gave up)."""
    deadline = time.time() + max_s
    while time.time() < deadline:
        try:
            st = api("/containers/trivy-scan/json").get("State") or {}
            if st.get("Status") in ("exited", "dead"):
                return int(st.get("ExitCode", -1))
        except Exception:
            pass                     # not created yet / proxy starting
        time.sleep(15)
    return -1


def build_lines(out_dir: str, now: str) -> tuple[list[str], dict]:
    lines, stats = [], {"images": 0, "errors": 0, "fixable_critical_images": 0}
    metas = sorted(glob.glob(f"{out_dir}/*.meta"), key=lambda p: int(os.path.basename(p).split(".")[0]))
    for meta in metas:
        n = meta[:-len(".meta")]
        image_id, _, name = open(meta).read().strip().partition(" ")
        stats["images"] += 1
        fields = [("ts", now), ("image", name or image_id), ("image_id", image_id[:19])]
        try:
            if os.path.exists(n + ".err"):
                raise ValueError("trivy failed")
            s = summarise(json.load(open(n + ".json")))
            fields += [("status", "ok")] + list(s.items())
            if s["fixable_critical"]:
                stats["fixable_critical_images"] += 1
        except (OSError, ValueError) as e:
            stats["errors"] += 1
            fields += [("status", "error"), ("err", str(e)[:120])]
        lines.append(" ".join(f"{k}={q(v)}" for k, v in fields))
    return lines, stats


def loki_push(url: str, host: str, lines: list[str]) -> None:
    base = time.time_ns()
    body = {"streams": [{"stream": {"job": "trivy", "host": host},
                         "values": [[str(base + i), ln] for i, ln in enumerate(lines)]}]}
    req = urllib.request.Request(url.rstrip("/") + "/loki/api/v1/push", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(10)


def pushover(title: str, msg: str) -> None:
    env = {}
    try:
        for ln in open(os.environ.get("PUSHOVER_FILE", "/pushover.env")):
            k, _, v = ln.strip().removeprefix("export ").partition("=")
            env[k.strip()] = v.strip().strip("'\"")
        data = urllib.parse.urlencode({"token": env["PUSHOVER_TOKEN"], "user": env["PUSHOVER_USER"],
                                       "title": title, "message": msg[:1000], "priority": 0}).encode()
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=data, timeout=15).read()
    except Exception as e:           # the fallback channel never fails the run
        print(f"level=warn msg=\"pushover failed\" err={q(e)}", flush=True)


def main() -> int:
    host = os.environ.get("NODE_NAME", "unknown")
    out_dir = os.environ.get("OUT_DIR", "/out")
    scan_exit = wait_for_scan(int(os.environ.get("SCAN_MAX_SECONDS", "10500")))
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines, stats = build_lines(out_dir, now) if os.path.exists(f"{out_dir}/done") else ([], {
        "images": 0, "errors": 0, "fixable_critical_images": 0})
    lines.append(f"ts={now} summary=1 images={stats['images']} errors={stats['errors']} "
                 f"fixable_critical_images={stats['fixable_critical_images']} scan_exit={scan_exit}")
    for ln in lines:
        print(ln, flush=True)
    hot = [ln for ln in lines if re.search(r"\bfixable_critical=[1-9]", ln)]
    mode = os.environ.get("PUSHOVER_MODE", "fallback")
    rc = 1 if scan_exit != 0 or stats["images"] == stats["errors"] else 0
    try:
        loki_push(os.environ.get("LOKI_URL", "http://10.1.20.21:3100"), host, lines)
    except Exception as e:
        print(f"level=error msg=\"loki push failed\" err={q(e)}", flush=True)
        mode, rc = ("always" if mode != "never" else mode), 2
    if hot and mode == "always":
        pushover(f"kdk trivy: fixable CRITICAL on {host}",
                 "\n".join(re.sub(r"^ts=\S+ ", "", ln)[:200] for ln in hot[:10]))
    return rc


if __name__ == "__main__":
    sys.exit(main())
