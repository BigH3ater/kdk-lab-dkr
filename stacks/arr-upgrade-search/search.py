#!/usr/bin/env python3
"""Ask Sonarr/Radarr to search for the files they already dislike.

Sonarr and Radarr score every on-disk file with the quality profile's custom
formats. A file with a negative score (today: the AV1 format at -10000) is
"cutoff unmet", but the *arr only replaces it when a better release happens
to show up on an RSS sync. Nothing ever searches backwards for it -- The
Terminal List S01E01-06 sat as AV1 from 2026-08-27 until a release appeared
on 2026-10-25. This job runs weekly and posts an EpisodeSearch / MoviesSearch
for exactly those episodes/movies, capped per run so indexers are not hammered.

Why not the built-in CutoffUnmetEpisodeSearch command: that searches every
cutoff-unmet item (1,496 episodes + 258 movies here, mostly quality cutoff),
which is far more indexer traffic than the goal needs.

Stdlib only. API keys come from the mounted (ro) config.xml files. DRY_RUN=1
reports only. Pushover gets a short summary when anything was searched or
when an API call failed.
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
MAX_PER_RUN = int(os.environ.get("MAX_PER_RUN", "20"))
MIN_SCORE = int(os.environ.get("MIN_SCORE", "0"))
SONARR = os.environ.get("SONARR_URL", "https://sonarr.kmkdp.com")
RADARR = os.environ.get("RADARR_URL", "https://radarr.kmkdp.com")
SONARR_CFG = os.environ.get("SONARR_CFG", "/sonarr-config/config.xml")
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


def sonarr_targets(key):
    out = []
    for s in req(f"{SONARR}/api/v3/series", key):
        if not s.get("monitored"):
            continue
        for f in req(f"{SONARR}/api/v3/episodefile?seriesId={s['id']}", key):
            score = f.get("customFormatScore")
            if score is not None and score < MIN_SCORE:
                out.append({"seriesId": s["id"], "fileId": f["id"], "score": score,
                            "name": f["path"].rsplit("/", 1)[-1],
                            "formats": [c["name"] for c in f.get("customFormats", [])]})
    # episode ids for the files (EpisodeSearch takes episodeIds)
    for t in out:
        eps = req(f"{SONARR}/api/v3/episode?seriesId={t['seriesId']}&episodeFileId={t['fileId']}", key)
        t["episodeIds"] = [e["id"] for e in eps]
    return out


def radarr_targets(key):
    out = []
    for m in req(f"{RADARR}/api/v3/movie", key):
        f = m.get("movieFile")
        if not f or not m.get("monitored"):
            continue
        score = f.get("customFormatScore")
        if score is not None and score < MIN_SCORE:
            out.append({"movieId": m["id"], "score": score, "name": f["path"].rsplit("/", 1)[-1],
                        "formats": [c["name"] for c in f.get("customFormats", [])]})
    return out


def main():
    skey, rkey = api_key(SONARR_CFG), api_key(RADARR_CFG)
    errors = []
    try:
        eps = sonarr_targets(skey)
    except Exception as e:  # noqa: BLE001
        errors.append(f"sonarr: {e}"); eps = []
    try:
        movs = radarr_targets(rkey)
    except Exception as e:  # noqa: BLE001
        errors.append(f"radarr: {e}"); movs = []

    log(f"targets: {len(eps)} episode files, {len(movs)} movies below score {MIN_SCORE} (cap {MAX_PER_RUN}/run, dry_run={DRY_RUN})")
    budget = MAX_PER_RUN
    searched = []
    for t in eps:
        if budget <= 0:
            break
        label = f"{t['name']} ({t['score']} {','.join(t['formats'])})"
        if not t["episodeIds"]:
            continue
        log(("WOULD search " if DRY_RUN else "search ") + label)
        if not DRY_RUN:
            try:
                req(f"{SONARR}/api/v3/command", skey, "POST", {"name": "EpisodeSearch", "episodeIds": t["episodeIds"]})
            except Exception as e:  # noqa: BLE001
                errors.append(f"sonarr EpisodeSearch {label}: {e}"); continue
        searched.append(label); budget -= 1
    for t in movs:
        if budget <= 0:
            break
        label = f"{t['name']} ({t['score']} {','.join(t['formats'])})"
        log(("WOULD search " if DRY_RUN else "search ") + label)
        if not DRY_RUN:
            try:
                req(f"{RADARR}/api/v3/command", rkey, "POST", {"name": "MoviesSearch", "movieIds": [t["movieId"]]})
            except Exception as e:  # noqa: BLE001
                errors.append(f"radarr MoviesSearch {label}: {e}"); continue
        searched.append(label); budget -= 1

    deferred = max(0, len(eps) + len(movs) - len(searched))
    summary = f"{'DRY RUN: ' if DRY_RUN else ''}searched {len(searched)}, deferred {deferred}, errors {len(errors)}"
    log(summary)
    if errors:
        for e in errors:
            log("ERROR", e)
        pushover("kdk-lab arr-upgrade-search FAILED", summary + "\n" + "\n".join(errors[:6]), priority=1)
    elif searched:
        pushover("kdk-lab arr-upgrade-search", summary + "\n" + "\n".join(searched[:10]))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
