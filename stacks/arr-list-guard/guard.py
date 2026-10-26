#!/usr/bin/env python3
"""Remove Indian-language movies that import lists pushed into Radarr.

The IMDb Popular and Top 250 lists regularly carry Hindi/Tamil/Telugu titles.
Radarr cannot filter IMDb lists by language, so this runs after each list sync
and deletes those movies again, with addImportExclusion=true so the list does
not re-add them next time. Guard rails: a movie is only removed when it has
no tags (Seerr requests are tagged with the requester) and no file on disk.
Everything else that matches the language set is reported, not touched.

Stdlib only. API key from the mounted (ro) config.xml. DRY_RUN=1 reports only.
Pushover gets a summary when anything was removed or an API call failed.
"""
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"
LANGUAGES = {x.strip() for x in os.environ.get(
    "LANGUAGES", "Hindi,Tamil,Telugu,Malayalam,Kannada,Bengali,Marathi,Urdu").split(",") if x.strip()}
RADARR = os.environ.get("RADARR_URL", "https://radarr.kmkdp.com")
RADARR_CFG = os.environ.get("RADARR_CFG", "/radarr-config/config.xml")
PUSHOVER_ENV = os.environ.get("PUSHOVER_ENV", "/kdk/pushover.env")
CTX = ssl.create_default_context()


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def api_key(path):
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", Path(path).read_text())
    if not m:
        raise RuntimeError(f"no <ApiKey> in {path}")
    return m.group(1)


def req(url, key, method="GET", body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("content-type", "application/json")
    r.add_header("X-Api-Key", key)
    with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def pushover(title, message, priority=0):
    creds = {}
    try:
        for line in Path(PUSHOVER_ENV).read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip().strip('"')
    except OSError:
        log("pushover.env unreadable; skipping notification")
        return
    tok, usr = creds.get("PUSHOVER_TOKEN"), creds.get("PUSHOVER_USER")
    if not tok or not usr:
        return
    data = urllib.parse.urlencode({"token": tok, "user": usr, "title": title,
                                   "message": message[:1000], "priority": priority}).encode()
    try:
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data, timeout=25, context=CTX).read()
    except Exception as e:  # noqa: BLE001
        log(f"pushover failed: {e}")


def main():
    key = api_key(RADARR_CFG)
    errors, removed, kept = [], [], []
    try:
        movies = req(f"{RADARR}/api/v3/movie", key)
    except Exception as e:  # noqa: BLE001
        log(f"ERROR radarr movie list: {e}")
        pushover("kdk-lab arr-list-guard FAILED", f"radarr unreachable: {e}", priority=1)
        sys.exit(1)

    hits = [m for m in movies if (m.get("originalLanguage") or {}).get("name") in LANGUAGES]
    log(f"{len(movies)} movies, {len(hits)} in {sorted(LANGUAGES)} (dry_run={DRY_RUN})")
    for m in hits:
        label = f"{m['title']} ({m.get('year')}, {m['originalLanguage']['name']})"
        if m.get("tags"):
            kept.append(f"{label} -- tagged, kept"); continue
        if m.get("hasFile"):
            kept.append(f"{label} -- has file, kept"); continue
        log(("WOULD remove+exclude " if DRY_RUN else "remove+exclude ") + label)
        if not DRY_RUN:
            try:
                req(f"{RADARR}/api/v3/movie/{m['id']}?deleteFiles=false&addImportExclusion=true", key, "DELETE")
            except Exception as e:  # noqa: BLE001
                errors.append(f"delete {label}: {e}"); continue
        removed.append(label)

    for k in kept:
        log("kept:", k)
    summary = f"{'DRY RUN: ' if DRY_RUN else ''}removed+excluded {len(removed)}, kept {len(kept)}, errors {len(errors)}"
    log(summary)
    if errors:
        for e in errors:
            log("ERROR", e)
        pushover("kdk-lab arr-list-guard FAILED", summary + "\n" + "\n".join(errors[:6]), priority=1)
    elif removed:
        pushover("kdk-lab arr-list-guard", summary + "\n" + "\n".join(removed[:10])
                 + ("\n\nkept (file on disk):\n" + "\n".join(x for x in kept if "has file" in x)[:300] if any("has file" in x for x in kept) else ""))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
