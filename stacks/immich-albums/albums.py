#!/usr/bin/env python3
"""immich-albums: nightly one-shot that turns Insta360 trip folders into albums.

Runs inside the pinned salvoxia/immich-folder-album-creator image (python is
already there) as the container command, under the image's tini. Order is
deterministic because this script triggers the library scan and waits for it:

  1. on-disk check: /mnt/insta360-flat (this container's own :ro bind) must hold at
     least one trip folder; an empty directory means the host NFS mount is gone;
  2. wait for immich-server (/api/server/ping), up to SERVER_WAIT_S;
  3. find the external library whose import paths contain ROOT_PATH (admin
     scan key) and check that the album key's user (Household) owns it;
  4. POST /api/libraries/<id>/scan, then poll GET /api/queues/library until
     active + waiting + delayed == 0 on 3 polls in a row (SCAN_TIMEOUT_S);
  5. offline check: Household assets marked offline (Immich trashes them). As
     many offline as files on disk = immich-server lost its view of the mount;
  6. run the tool once (/script/immich_auto_album.sh, UNATTENDED from env):
     one album per top-level folder, named exactly like the folder, shared with
     SHARE_WITH as editors; re-checked every night (UPDATE_ALBUM_PROPS_MODE=2).

Any failure -> one Pushover message (pushover.env, the trivy-scan pattern) and
exit 1. The tool itself logs "fatal" and still exits 0 on API errors inside a
run, so its output is scanned for fatal/critical lines and tracebacks too.
Stdlib only. The API keys never reach a command line: the Household key is
handed to the tool as a 0600 file on the /tmp tmpfs (API_KEY_FILE).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = os.environ.get("IMMICH_URL", "http://immich-server:2283").rstrip("/") + "/api"
ROOT = os.environ.get("ROOT_PATH", "/mnt/insta360-flat").rstrip("/")
ALBUM_KEY = os.environ.get("IMMICH_ALBUMS_API_KEY", "")
SCAN_KEY = os.environ.get("IMMICH_SCAN_API_KEY", "")
PUSHOVER_FILE = os.environ.get("PUSHOVER_FILE", "/pushover.env")
SERVER_WAIT_S = int(os.environ.get("SERVER_WAIT_S", "600"))
SCAN_TIMEOUT_S = int(os.environ.get("SCAN_TIMEOUT_S", "1800"))
POLL_S = 5
KEY_FILE = os.environ.get("KEY_FILE", "/tmp/household.key")
TOOL = os.environ.get("TOOL", "/script/immich_auto_album.sh")
TITLE = "immich-albums failed"


class Fail(Exception):
    pass


def log(msg, level="info"):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    msg = str(msg).replace('"', "'")
    print(f'time="{ts}" level={level} src=immich-albums msg="{msg}"', flush=True)


def pushover(message):
    creds = {}
    try:
        for line in open(PUSHOVER_FILE, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip().strip('"')
    except OSError as e:
        log(f"pushover.env unreadable ({e.strerror}); not notified", "error")
        return
    tok, usr = creds.get("PUSHOVER_TOKEN"), creds.get("PUSHOVER_USER")
    if not tok or not usr:
        log("pushover.env has no PUSHOVER_TOKEN/PUSHOVER_USER; not notified", "error")
        return
    data = urllib.parse.urlencode(
        {"token": tok, "user": usr, "title": TITLE, "message": message[:1000]}
    ).encode()
    try:
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data, timeout=25).read()
    except Exception as e:  # noqa: BLE001
        log(f"pushover failed: {e}", "error")


def api(method, path, key=None, body=None, timeout=30):
    """One Immich API call. Returns parsed JSON (None for an empty body)."""
    headers = {"Accept": "application/json"}
    if key:
        headers["x-api-key"] = key
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "replace").replace('"', "'")
        raise Fail(f"{method} {path}: HTTP {e.code} {detail}") from None
    except (urllib.error.URLError, OSError) as e:
        raise Fail(f"{method} {path}: {e}") from None
    return json.loads(raw) if raw.strip() else None


def disk_inventory():
    """(trip folders, files) under ROOT as this container sees it."""
    try:
        entries = [e for e in os.scandir(ROOT) if not e.name.startswith(".")]
    except OSError as e:
        raise Fail(f"{ROOT} unreadable: {e.strerror}") from None
    trips = [e.name for e in entries if e.is_dir()]
    files = 0
    for _dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        files += sum(1 for f in filenames if not f.startswith("."))
    return trips, files


def wait_for_server():
    deadline = time.monotonic() + SERVER_WAIT_S
    last = ""
    while time.monotonic() < deadline:
        try:
            if (api("GET", "/server/ping", timeout=10) or {}).get("res") == "pong":
                return
        except Fail as e:
            last = str(e)
        time.sleep(POLL_S)
    raise Fail(f"immich-server not answering after {SERVER_WAIT_S}s ({last})")


def find_library(owner_id):
    libs = api("GET", "/libraries", SCAN_KEY) or []
    mine = [lib for lib in libs if ROOT in [p.rstrip("/") for p in lib.get("importPaths", [])]]
    if not mine:
        raise Fail(f"no external library with import path {ROOT}; create it (docs/services/immich.md)")
    if len(mine) > 1:
        raise Fail(f"{len(mine)} libraries import {ROOT}; keep exactly one")
    lib = mine[0]
    if lib.get("ownerId") != owner_id:
        raise Fail("the Insta360 library is not owned by the album key's user (Household)")
    return lib


def scan_and_wait(lib_id):
    api("POST", f"/libraries/{lib_id}/scan", SCAN_KEY)
    log(f"library scan queued ({lib_id})")
    start = time.monotonic()
    idle = 0
    while idle < 3:
        if time.monotonic() - start > SCAN_TIMEOUT_S:
            raise Fail(f"library queue still busy after {SCAN_TIMEOUT_S}s")
        time.sleep(POLL_S)
        q = api("GET", "/queues/library", SCAN_KEY) or {}
        if q.get("isPaused"):
            raise Fail("library queue is paused (Administration -> Jobs)")
        s = q.get("statistics", {})
        busy = s.get("active", 0) + s.get("waiting", 0) + s.get("delayed", 0)
        idle = idle + 1 if busy == 0 else 0
    log(f"library scan finished in {int(time.monotonic() - start)}s")


def offline_count():
    # Legacy flat search fields (still accepted by v3.4; the tool uses them too).
    # Offline external assets are also trashed, hence withDeleted.
    r = api("POST", "/search/metadata", ALBUM_KEY,
            {"isOffline": True, "withDeleted": True, "size": 1000}) or {}
    return int(r.get("assets", {}).get("count", 0))


def run_tool():
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(ALBUM_KEY)
    env = {k: v for k, v in os.environ.items()
           if k not in ("IMMICH_ALBUMS_API_KEY", "IMMICH_SCAN_API_KEY", "API_KEY")}
    env.update({"API_URL": API + "/", "API_KEY_FILE": KEY_FILE, "ROOT_PATH": ROOT})
    bad, missing_users = [], set()
    p = subprocess.Popen([TOOL], cwd=os.path.dirname(TOOL), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in p.stdout:
        sys.stdout.write(line)
        low = line.lower()
        if "level=critical" in low or "level=fatal" in low or "level=error" in low or low.startswith("traceback"):
            bad.append(line.strip()[:200])
        if "to share album" in low and "does not exist" in low:
            missing_users.add(line.split("User ", 1)[-1].split(" to share", 1)[0])
    rc = p.wait()
    sys.stdout.flush()
    if missing_users:
        log("share users not in Immich yet (shared on a later run once they exist): "
            + ", ".join(sorted(missing_users)), "warning")
    if rc != 0 or bad:
        raise Fail(f"album tool exit={rc}; " + (bad[0] if bad else "see container log"))


def main():
    for name, val in (("IMMICH_ALBUMS_API_KEY", ALBUM_KEY), ("IMMICH_SCAN_API_KEY", SCAN_KEY)):
        if len(val) < 20 or val == "placeholder":
            raise Fail(f"{name} missing or not rendered (op inject in the Komodo pre_deploy)")
    trips, files = disk_inventory()
    if not trips:
        raise Fail(f"{ROOT} has no trip folders: host NFS mount missing? (mnt-insta360\\x2dflat.mount)")
    log(f"on disk: {len(trips)} trip folder(s), {files} file(s)")
    wait_for_server()
    me = api("GET", "/users/me", ALBUM_KEY)
    lib = find_library(me["id"])
    scan_and_wait(lib["id"])
    offline = offline_count()
    if offline and offline >= files:
        raise Fail(f"{offline} Insta360 assets offline vs {files} files on disk: immich-server "
                   "probably lost the mount; check it, then redeploy immich (docs/services/immich.md)")
    if offline:
        log(f"{offline} asset(s) offline (renamed/removed files; Immich purges them with the trash after 30 days)")
    run_tool()
    log("done")


if __name__ == "__main__":
    try:
        main()
    except Fail as e:
        log(str(e), "error")
        pushover(str(e))
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        log(f"unexpected: {e!r}", "error")
        pushover(f"unexpected: {e!r}")
        sys.exit(1)
