#!/usr/bin/env python3
"""Tdarr health-check remediation for kdk-lab.

Finds files Tdarr marked HealthCheck == "Error" (genuinely unreadable/corrupt
per FFprobe) and, in Radarr (movies) or Sonarr (TV), blocklists the grabbed
release, deletes the bad file, and triggers a fresh search -- so a corrupt
grab self-heals instead of sitting broken.

WHY ONLY HealthCheck errors: Tdarr's other failure state,
TranscodeDecisionMaker == "Transcode error", is usually an INFRA problem (the
DEE wrapper being down took out ~78 good files on 2026-09-11). Blocklisting +
redownloading in that case would churn perfectly good releases across the whole
library. tdarr-verify already alerts on transcode errors; this tool never
touches them.

SAFETY:
  * DRY_RUN=1 (default) -- resolve + report what it WOULD do, mutate nothing.
  * Circuit breaker (MAX_BATCH) -- if more files fail at once than a single
    outage of bad releases plausibly explains, that's systemic (bad ffmpeg,
    storage/mount fault). Page for manual review; do not mass-delete.
  * Per-title cap (MAX_ATTEMPTS) -- if the SAME movie/episode keeps coming back
    corrupt after N blocklist+redownload cycles, every available release may be
    bad. Give up and page instead of looping forever.

Runs one-shot on kdk-dkr-01 via the tdarr-remediate Komodo procedure. Stdlib
only. API keys are read from the mounted (ro) Radarr/Sonarr config.xml, so no
new secrets are introduced. Pushover creds come from /kdk/pushover.env.
"""
import json
import os
import re
import ssl
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ---- config (env-overridable) ----------------------------------------------
DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
MAX_BATCH = int(os.environ.get("MAX_BATCH", "5"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
TDARR = os.environ.get("TDARR_URL", "http://localhost:8266")
RADARR = os.environ.get("RADARR_URL", "https://radarr.kmkdp.com")
SONARR = os.environ.get("SONARR_URL", "https://sonarr.kmkdp.com")
MOVIES_PREFIX = os.environ.get("MOVIES_PREFIX", "/data/media/movies")
TV_PREFIX = os.environ.get("TV_PREFIX", "/data/media/tv")
RADARR_CFG = os.environ.get("RADARR_CFG", "/radarr-config/config.xml")
SONARR_CFG = os.environ.get("SONARR_CFG", "/sonarr-config/config.xml")
STATE_FILE = Path(os.environ.get("STATE_FILE", "/kdk/tdarr-remediate.state"))
PUSHOVER_ENV = os.environ.get("PUSHOVER_ENV", "/kdk/pushover.env")

CTX = ssl.create_default_context()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def api_key(path):
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", Path(path).read_text())
    if not m:
        raise RuntimeError(f"no <ApiKey> in {path}")
    return m.group(1)


def req(url, key=None, method="GET", body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("content-type", "application/json")
    if key:
        r.add_header("X-Api-Key", key)
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def pushover(title, message, priority=1):
    creds = {}
    try:
        for line in Path(PUSHOVER_ENV).read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip().strip('"')
    except OSError as e:
        log(f"pushover: cannot read {PUSHOVER_ENV}: {e}")
        return
    tok, usr = creds.get("PUSHOVER_TOKEN"), creds.get("PUSHOVER_USER")
    if not (tok and usr):
        log("pushover: missing token/user")
        return
    import urllib.parse
    form = urllib.parse.urlencode(
        {"token": tok, "user": usr, "title": title, "message": message, "priority": priority}
    ).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request("https://api.pushover.net/1/messages.json", data=form),
            timeout=15,
            context=CTX,
        )
    except Exception as e:  # noqa: BLE001 - alerting must never crash the run
        log(f"pushover send failed: {e}")


def load_state():
    try:
        s = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        s = {}
    s.setdefault("attempts", {})   # key -> int, per-title remediation count
    s.setdefault("reported", [])   # keys already notified in dry-run
    s.setdefault("deleted", [])    # paths deleted live but still lingering in Tdarr
    return s


def save_state(s):
    try:
        STATE_FILE.write_text(json.dumps(s, indent=2))
    except OSError as e:
        log(f"state write failed: {e}")


# ---- *arr resolution -------------------------------------------------------
def resolve_movie(path, key):
    """path -> dict(app, key, title, file_id, search, grab_id, grab_src) or None."""
    for m in req(f"{RADARR}/api/v3/movie", key):
        mf = m.get("movieFile") or {}
        if mf.get("path") == path:
            hist = req(f"{RADARR}/api/v3/history/movie?movieId={m['id']}", key) or []
            grabs = [h for h in hist if h.get("eventType") == "grabbed"]
            g = grabs[0] if grabs else None
            return {
                "app": "radarr",
                "key": f"radarr:{m['id']}",
                "title": f"{m.get('title')} ({m.get('year')})",
                "file_id": mf["id"],
                "delete": f"{RADARR}/api/v3/moviefile/{mf['id']}",
                "search": (f"{RADARR}/api/v3/command", {"name": "MoviesSearch", "movieIds": [m["id"]]}),
                "grab_id": g["id"] if g else None,
                "grab_src": g.get("sourceTitle") if g else None,
                "blocklist": (f"{RADARR}/api/v3/history/failed/{g['id']}" if g else None),
            }
    return None


def resolve_episode(path, key):
    series = req(f"{SONARR}/api/v3/series", key)
    s = next((x for x in series if path.startswith(x["path"] + "/")), None)
    if not s:
        return None
    efs = req(f"{SONARR}/api/v3/episodefile?seriesId={s['id']}", key) or []
    ef = next((e for e in efs if e.get("path") == path), None)
    if not ef:
        return None
    eps = [e for e in (req(f"{SONARR}/api/v3/episode?seriesId={s['id']}", key) or [])
           if e.get("episodeFileId") == ef["id"]]
    ep_ids = sorted(e["id"] for e in eps)
    tag = ",".join(f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}" for e in eps)
    g = None
    if ep_ids:
        h = req(f"{SONARR}/api/v3/history?episodeId={ep_ids[0]}&pageSize=50", key) or {}
        grabs = [r for r in h.get("records", []) if r.get("eventType") == "grabbed"]
        g = grabs[0] if grabs else None
    # Key on the stable episode id(s), NOT the episodeFile id -- the file id
    # changes on every redownload, so keying on it would reset the per-title
    # attempt counter each cycle and defeat MAX_ATTEMPTS.
    return {
        "app": "sonarr",
        "key": "sonarr:" + "-".join(str(i) for i in ep_ids),
        "title": f"{s.get('title')} {tag}".strip(),
        "file_id": ef["id"],
        "delete": f"{SONARR}/api/v3/episodefile/{ef['id']}",
        "search": (f"{SONARR}/api/v3/command", {"name": "EpisodeSearch", "episodeIds": ep_ids}),
        "grab_id": g["id"] if g else None,
        "grab_src": g.get("sourceTitle") if g else None,
        "blocklist": (f"{SONARR}/api/v3/history/failed/{g['id']}" if g else None),
        "search_ok": bool(ep_ids),
    }


def remediate(item, key):
    """Blocklist grab, delete file, trigger a fresh search."""
    steps = []
    if item.get("blocklist"):
        req(item["blocklist"], key, method="POST", body=None)
        steps.append(f"blocklisted grab {item['grab_id']}")
    else:
        steps.append("no grab record to blocklist")
    req(item["delete"], key, method="DELETE")
    steps.append(f"deleted file {item['file_id']}")
    url, cmd = item["search"]
    req(url, key, method="POST", body=cmd)
    steps.append("triggered search")
    # No need to touch Tdarr's DB: once the file is gone from disk Tdarr drops
    # the record itself on its next scan (observed within a minute). The
    # deleted-path suppression in main() covers that brief window.
    return "; ".join(steps)


def main():
    rk = api_key(RADARR_CFG)
    sk = api_key(SONARR_CFG)
    state = load_state()

    dump = req(f"{TDARR}/api/v2/cruddb", method="POST",
               body={"data": {"collection": "FileJSONDB", "mode": "getAll"}})
    errors = [r for r in dump if r.get("HealthCheck") == "Error"]
    log(f"tdarr files={len(dump)} health-error={len(errors)} dry_run={DRY_RUN}")

    # Resolve each error file to its *arr identity.
    resolved, unmatched = [], []
    for r in errors:
        path = r.get("file", "")
        if path.startswith(MOVIES_PREFIX):
            item = resolve_movie(path, rk)
            key = rk
        elif path.startswith(TV_PREFIX):
            item = resolve_episode(path, sk)
            key = sk
        else:
            item, key = None, None
        if item:
            item["_path"] = path
            item["_apikey"] = key
            resolved.append(item)
        else:
            unmatched.append(path)

    current_keys = {it["key"] for it in resolved}
    error_paths = {r.get("file", "") for r in errors}
    # Forget dry-run notifications for titles no longer failing, so a fresh
    # failure of the same title re-notifies.
    state["reported"] = [k for k in state["reported"] if k in current_keys]
    # A path we deleted lingers in Tdarr's DB until its next library scan drops
    # the missing file. Keep suppressing "no *arr match" for it while it lingers;
    # once Tdarr forgets it (leaves the error set) stop tracking it.
    state["deleted"] = [p for p in state["deleted"] if p in error_paths]
    unmatched = [u for u in unmatched if u not in state["deleted"]]

    # Circuit breaker: too many at once is systemic, not per-release rot.
    if len(resolved) > MAX_BATCH:
        names = "\n".join(f"- {it['title']}" for it in resolved[:12])
        msg = (f"{len(resolved)} files failed Tdarr health check at once "
               f"(> MAX_BATCH={MAX_BATCH}). Likely systemic (ffmpeg/storage), not "
               f"bad releases. NOT auto-remediating. Review manually:\n{names}")
        log("CIRCUIT BREAKER: " + msg)
        pushover("kdk Tdarr: health failures spike", msg, 1)
        save_state(state)
        return

    if not resolved and not unmatched:
        log("clean -- no health-error files")
        save_state(state)
        return

    acted, would, gaveup, skipped_reported = [], [], [], 0
    for it in resolved:
        attempts = state["attempts"].get(it["key"], 0)
        label = f"{it['app']} {it['title']}"
        if attempts >= MAX_ATTEMPTS:
            if it["key"] not in state["reported"]:
                gaveup.append(f"{label} (failed {attempts}x -- every release corrupt?)")
                state["reported"].append(it["key"])
            continue
        if DRY_RUN:
            if it["key"] in state["reported"]:
                skipped_reported += 1
                continue
            src = it.get("grab_src") or "(no grab record)"
            would.append(f"{label}\n    release: {src}\n    would: blocklist grab "
                         f"{it['grab_id']}, delete file {it['file_id']}, re-search")
            state["reported"].append(it["key"])
        else:
            summary = remediate(it, it["_apikey"])
            state["attempts"][it["key"]] = attempts + 1
            if it["_path"] not in state["deleted"]:
                state["deleted"].append(it["_path"])  # suppress lingering-record noise
            acted.append(f"{label} [try {attempts + 1}]: {summary}")
            log(f"REMEDIATED {label}: {summary}")

    save_state(state)

    # ---- notify (only on something new/actionable; silent otherwise) -------
    parts = []
    if acted:
        parts.append("Remediated (blocklist+delete+redownload):\n" + "\n".join(f"- {a}" for a in acted))
    if would:
        parts.append("DRY-RUN -- would remediate:\n" + "\n".join(f"- {w}" for w in would))
    if gaveup:
        parts.append("GAVE UP (hit per-title cap -- manual review):\n" + "\n".join(f"- {g}" for g in gaveup))
    if unmatched:
        parts.append("Health-error files with no Radarr/Sonarr match (manual):\n"
                     + "\n".join(f"- {u.rsplit('/', 1)[-1]}" for u in unmatched[:10]))
    if parts:
        title = "kdk Tdarr remediation" + (" (DRY-RUN)" if DRY_RUN else "")
        pushover(title, "\n\n".join(parts), 1 if (acted or gaveup) else 0)
        log("NOTIFY:\n" + "\n\n".join(parts))
    else:
        log(f"nothing new to report (already-reported={skipped_reported})")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        log(f"HTTP {e.code} {e.reason} @ {e.url}")
        pushover("kdk Tdarr remediation ERROR", f"HTTP {e.code} {e.reason}\n{e.url}", 1)
        sys.exit(0)  # exit clean so docker-event-watch doesn't page a "crash"
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {e}")
        pushover("kdk Tdarr remediation ERROR", str(e), 1)
        sys.exit(0)
