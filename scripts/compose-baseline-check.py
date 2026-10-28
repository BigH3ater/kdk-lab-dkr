#!/usr/bin/env python3
"""Check every stacks/*/compose.yaml service against the compose hardening baseline.

The standard, the item names and the exception mechanism are documented in
docs/reference/compose-baseline.md. Python 3 stdlib only. The compose files are
parsed with PyYAML when it is importable (fast path; anchors and `<<:` merges
are resolved by the YAML loader) and otherwise with
`docker compose -f <file> config --format json --no-interpolate
--no-env-resolution --no-path-resolution` (no .env or host files needed).
Either way the MERGED service is evaluated, so `x-hardened` anchors count.

Exit codes: 0 = clean (or no regressions with --allow-known), 1 = gaps or
regressions, 2 = usage error or a compose file that could not be loaded.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter

# Required on every service unless listed in `kdk.baseline.skip`.
REQUIRED = ["no-new-privileges", "cap_drop", "read_only", "pids_limit",
            "mem_limit", "logging", "restart", "image-pin", "healthcheck"]
# Forbidden unless the service carries a `kdk.baseline.exception` reason for it.
GATED = ["privileged", "host-net", "devices", "docker-sock"]
# Warn-only (never fails): secret-ish bind mount without :ro.
WARN = "rw-mount"
EXC_LABEL, SKIP_LABEL = "kdk.baseline.exception", "kdk.baseline.skip"
# x.y or newer (v-prefix ok) or a date tag (homebridge 2026-08-17).
TAG_RE = re.compile(r"^(v?\d+\.\d+|\d{4}-\d{2}-\d{2})")
DOCKER_CFG = ["config", "--format", "json", "--no-interpolate",
              "--no-env-resolution", "--no-path-resolution"]


def pick_loader(choice):
    if choice != "auto":
        return choice
    try:
        import yaml  # noqa: F401
        return "yaml"
    except ImportError:
        return "docker"


def load_compose(path, loader):
    if loader == "yaml":
        import yaml
        with open(path) as f:
            return yaml.safe_load(f) or {}
    p = subprocess.run(["docker", "compose", "-f", path] + DOCKER_CFG,
                       capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr.strip().splitlines() or ["docker compose config failed"])[-1]
        raise RuntimeError(err)
    return json.loads(p.stdout)


def labels_of(svc):
    raw = svc.get("labels") or {}
    if isinstance(raw, list):
        raw = dict(x.split("=", 1) if "=" in x else (x, "") for x in raw)
    return {str(k): str(v) for k, v in raw.items()}


def waivers(lbl):
    """Return ({item: reason}, bad_label_entries) from the two baseline labels."""
    waived, bad = {}, []
    for part in lbl.get(EXC_LABEL, "").split(";"):
        if not part.strip():
            continue
        key, _, why = part.partition(":")
        key, why = key.strip(), why.strip()
        if key not in GATED + [WARN] or not why:
            bad.append(part.strip())
        else:
            waived[key] = why
    for item in lbl.get(SKIP_LABEL, "").split(","):
        item = item.strip()
        if not item:
            continue
        if item not in REQUIRED:
            bad.append(item)
        else:
            waived[item] = "skip"
    return waived, bad


def bind_mounts(svc):
    """Yield (source, read_only) for every bind mount, short or long syntax."""
    for v in svc.get("volumes") or []:
        if isinstance(v, str):
            parts = v.split(":")
            if len(parts) < 2:
                continue                                  # anonymous volume
            src, ro = parts[0], len(parts) > 2 and "ro" in parts[2].split(",")
            if src.startswith(("/", ".", "~", "$")):
                yield src, ro
        elif v.get("type") == "bind":
            yield str(v.get("source") or ""), bool(v.get("read_only"))


def image_pinned(svc, built=()):
    """x.y tag (not latest/stable), or an immutable @sha256 digest, or built here."""
    image = str(svc.get("image") or "")
    if svc.get("build") or (image and image in built):
        return True     # built from this stack; the Dockerfile FROM line is the pin
    name, _, digest = image.partition("@")
    if digest.startswith("sha256:"):
        return True
    last = name.rsplit("/", 1)[-1]
    tag = last.split(":", 1)[1] if ":" in last else ""
    return tag not in ("", "latest", "stable") and bool(TAG_RE.match(tag))


def is_secretish(src):
    base = os.path.basename(src.rstrip("/")).lower()
    return (src == "/etc" or src.startswith("/etc/")
            or re.match(r"^/opt/kdk-lab/[^/]+/secrets", src) is not None
            or base.endswith(".env") or "secret" in base)


def evaluate(svc, built=()):
    """Return (missing items, waived {item: reason}, warnings) for one merged service."""
    lbl = labels_of(svc)
    waived, bad = waivers(lbl)
    limits = ((svc.get("deploy") or {}).get("resources") or {}).get("limits") or {}
    log = svc.get("logging") or {}
    opts = log.get("options") or {}
    hc = svc.get("healthcheck") or {}
    restart = svc.get("restart")
    restart = "no" if restart is False else str(restart or "")
    mounts = list(bind_mounts(svc))
    reserved = (((svc.get("deploy") or {}).get("resources") or {})
                .get("reservations") or {}).get("devices")
    ok = {
        "no-new-privileges": any(str(o).replace("=", ":") in
                                 ("no-new-privileges", "no-new-privileges:true")
                                 for o in svc.get("security_opt") or []),
        "cap_drop": "ALL" in [str(c).upper() for c in svc.get("cap_drop") or []],
        "read_only": svc.get("read_only") is True,
        "pids_limit": svc.get("pids_limit") not in (None, "") or "pids" in limits,
        "mem_limit": svc.get("mem_limit") not in (None, "") or "memory" in limits,
        "logging": log.get("driver") in ("json-file", "local")
                   and "max-size" in opts and "max-file" in opts,
        "restart": restart in ("unless-stopped", "no") or restart.startswith("on-failure"),
        "image-pin": image_pinned(svc, built),
        "healthcheck": bool(hc) and not hc.get("disable")
                       and hc.get("test") not in (["NONE"], "NONE"),
        # gated: True = feature not used
        "privileged": svc.get("privileged") is not True,
        "host-net": svc.get("network_mode") != "host",
        "devices": not svc.get("devices") and not reserved,
        "docker-sock": not any(s.endswith("docker.sock") for s, _ in mounts),
    }
    missing = [i for i in REQUIRED + GATED if not ok[i] and i not in waived]
    if bad:
        missing.append("bad-label")
    warns = [] if WARN in waived else [
        f"{WARN} {s}" for s, ro in mounts if not ro and is_secretish(s)]
    warns += [f"bad-label {b!r}: exception needs '<{'|'.join(GATED + [WARN])}>: <why>', "
              f"skip takes {','.join(REQUIRED)}" for b in bad]
    used = {i: r for i, r in waived.items()
            if i == WARN or (i in ok and not ok[i])}
    return missing, used, warns


def scan(stacks_dir, only, loader):
    """Yield (stack, service, missing, waived, warns, path); load errors -> 'load-error'."""
    for path in sorted(glob.glob(os.path.join(stacks_dir, "*", "compose.yaml"))):
        stack = os.path.basename(os.path.dirname(path))
        if only and stack not in only:
            continue
        try:
            services = (load_compose(path, loader).get("services") or {})
        except Exception as e:  # noqa: BLE001 - report and keep going
            yield stack, "*", ["load-error"], {}, [str(e)], path
            continue
        built = {s.get("image") for s in services.values() if s and s.get("build")}
        for name in sorted(services):
            yield (stack, name) + evaluate(services[name] or {}, built) + (path,)


def read_known(path):
    known = {}
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                key, _, items = line.partition(":")
                known[key.strip()] = {i.strip() for i in items.split(",") if i.strip()}
    return known


def write_known(path, rows):
    with open(path, "w") as f:
        f.write("# Known compose-baseline gaps (stack/service: items). Generated by\n"
                "#   python3 scripts/compose-baseline-check.py --write-known "
                "scripts/compose-baseline-known-gaps.txt\n"
                "# The Action fails only on gaps NOT listed here. Regenerate (and commit)\n"
                "# whenever a hardening PR closes gaps; never add lines by hand to\n"
                "# silence a regression -- use a documented skip/exception label.\n")
        for r in rows:
            if r["missing"]:
                f.write(f"{r['stack']}/{r['service']}: {','.join(r['missing'])}\n")


def print_table(rows, show_all):
    head = ("stack", "service", "missing items", "exception")
    lines = []
    for r in rows:
        miss = ",".join(("+" + i if i in r["new"] else i) for i in r["missing"])
        exc = "; ".join(f"{k}: {v}" if v != "skip" else f"skip {k}"
                        for k, v in sorted(r["waived"].items()))
        if show_all or miss or exc:
            lines.append((r["stack"], r["service"], miss or "-", exc or "-"))
    widths = [max(len(x[i]) for x in [head] + lines) for i in range(3)]
    for ln in [head] + lines:
        print(" | ".join(c.ljust(w) for c, w in zip(ln[:3], widths)) + " | " + ln[3])
    for r in rows:
        for w in r["warns"]:
            print(f"warning: {r['stack']}/{r['service']}: {w}")


def print_summary(rows):
    miss = Counter(i for r in rows for i in r["missing"])
    waived = Counter(i for r in rows for i in r["waived"])
    print(f"\nservices: {len(rows)}, clean: {sum(not r['missing'] for r in rows)}, "
          f"warnings: {sum(len(r['warns']) for r in rows)}")
    print(f"{'item':<18} {'missing':>7} {'waived':>7}")
    for item in REQUIRED + GATED + ["bad-label", "load-error"]:
        if miss[item] or waived[item] or item in REQUIRED + GATED:
            print(f"{item:<18} {miss[item]:>7} {waived[item]:>7}")
    per_stack = Counter()
    for r in rows:
        per_stack[r["stack"]] += len(r["missing"])
    print("\ntop stacks by gap count:")
    for stack, n in per_stack.most_common(10):
        if n:
            print(f"  {stack:<26} {n}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        epilog="Standard: docs/reference/compose-baseline.md")
    ap.add_argument("--stacks-dir", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "stacks"),
        help="directory holding <stack>/compose.yaml (default: the repo's stacks/)")
    ap.add_argument("--stack", action="append", default=[],
                    help="limit to this stack directory name (repeatable)")
    ap.add_argument("--summary", action="store_true", help="print counts per item")
    ap.add_argument("--all", action="store_true", help="also list clean services")
    ap.add_argument("--allow-known", metavar="FILE",
                    help="fail only on gaps not listed in FILE (stack/service: items)")
    ap.add_argument("--write-known", metavar="FILE",
                    help="write the current gaps to FILE and exit 0")
    ap.add_argument("--loader", choices=["auto", "yaml", "docker"], default="auto",
                    help="auto = PyYAML if importable, else `docker compose config`")
    a = ap.parse_args()
    if a.write_known and a.stack:
        ap.error("--write-known covers the whole tree; drop --stack")
    loader = pick_loader(a.loader)
    known = read_known(a.allow_known) if a.allow_known else None
    rows = []
    for stack, svc, missing, waived, warns, path in scan(a.stacks_dir, a.stack, loader):
        key = f"{stack}/{svc}"
        new = set(missing) - (known.get(key, set()) if known is not None else set())
        rows.append(dict(stack=stack, service=svc, missing=missing, waived=waived,
                         warns=warns, new=new if known is not None else set(), path=path))
    if not rows:
        print("no services found", file=sys.stderr)
        return 2
    print(f"compose-baseline-check: {len(rows)} services, loader={loader}")
    print_table(rows, a.all)
    if a.summary:
        print_summary(rows)
    if a.write_known:
        write_known(a.write_known, rows)
        print(f"\nwrote {a.write_known}")
        return 0
    load_errors = [r for r in rows if "load-error" in r["missing"]]
    if known is None:
        bad = [r for r in rows if r["missing"]]
        print(f"\n{len(bad)} service(s) miss baseline items." if bad else "\nclean.")
        return 2 if load_errors else (1 if bad else 0)
    regressions = [r for r in rows if r["new"]]
    seen = {f"{r['stack']}/{r['service']}" for r in rows}
    shrunk = [r for r in rows if set(known.get(f"{r['stack']}/{r['service']}", ()))
              - set(r["missing"])]
    gone = [k for k in known if k.split("/", 1)[0] in {r["stack"] for r in rows}
            and k not in seen]
    for r in regressions:
        msg = f"{r['stack']}/{r['service']}: new baseline gap(s): {', '.join(sorted(r['new']))}"
        print(f"::error file={os.path.relpath(r['path'])}::{msg}"
              if os.environ.get("GITHUB_ACTIONS") else f"REGRESSION {msg}")
    if shrunk or gone:
        print(f"\nhint: {len(shrunk) + len(gone)} known-gap line(s) shrank or vanished; "
              f"regenerate and commit: python3 scripts/compose-baseline-check.py "
              f"--write-known {a.allow_known}")
    if load_errors:
        return 2
    print(f"\n{len(regressions)} regression(s) against {a.allow_known}." if regressions
          else f"\nno regressions against {a.allow_known}.")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
