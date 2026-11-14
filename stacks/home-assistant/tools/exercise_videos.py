#!/usr/bin/env python3
"""Plan videos as files in a private Jellyfin "Exercise" library, plus one playlist per person and day.

    # on kdk-dkr-01 (the media share is rw there), or from the laptop with --host:
    python3 exercise_videos.py download                       # dry run: what is missing / extra
    python3 exercise_videos.py download --apply               # yt-dlp the missing files
    python3 exercise_videos.py download --apply --prune       # ... and delete files no longer in the plan
    python3 exercise_videos.py download --host kdkadmin@10.1.20.20 --apply

    # anywhere that reaches Jellyfin (key from the environment only):
    JELLYFIN_API_KEY=... python3 exercise_videos.py jellyfin            # dry run
    JELLYFIN_API_KEY=... python3 exercise_videos.py jellyfin --apply    # scan, playlists, wger_videos.json
    ... jellyfin --apply --prune                              # also delete playlists it made that left the plan

The source is wger_plan.json (each exercise's YouTube `video`). Runbook:
docs/runbooks/exercise-videos.md.

download: one flat directory (default /mnt/media/exercise), one file per plan exercise named
"<plan key> [<youtube id>].mp4". The name never depends on the YouTube title, and a file is
never renamed: Jellyfin derives the item id from the path, so a changed name would break the
wger links. A replaced video has a new YouTube id, so it is a new file (the old one is listed
as extra, deleted only with --prune). yt-dlp runs as a one-shot container (image pinned in
yt-dlp/Dockerfile) as uid:gid 1000:1000 (the media stack's PUID/PGID): H.264 + AAC in mp4, at
most 1080p, the plan's exercise name as the embedded title. --prune only ever deletes files
whose name matches the pattern above; anything else in the directory is reported, not touched.
A failed video (YouTube's intermittent HTTP 403) is retried (ATTEMPTS runs, growing pause) and
never stops the run: the rest still download, then every video still missing is listed and the
exit code is 1. Afterwards the tool removes its own temp dir <dir>/.incoming (that path only,
and only when it holds no finished video).

jellyfin: finds the library by name (default "Exercise", type Home videos), refuses to go on
when any account other than the plan's people can see it, starts a scan of that library only
and waits until every plan file is an item, then creates or updates one playlist per person
and day: "<name> · <day> <label>" (e.g. "Jacob · Mon Lower A"), items in workout order, owned
by that person, not public, no shares. Days with the same video list share one playlist
("Rachel · Wed/Sat/Sun Daily parts"). Writes wger_videos.json (plan key -> item id, plus the
playlist ids it manages) so gen_wger.py can build the Video: line without a Jellyfin key.
Users are looked up by username at run time (the plan's people logins).

Dry run by default for both. Config from the environment only: JELLYFIN_API_KEY (never argv),
JELLYFIN_URL (API address, default https://jellyfin.kmkdp.com).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

TOOLS = Path(__file__).resolve().parent
PLAN = TOOLS / "wger_plan.json"
VIDEOS = TOOLS / "wger_videos.json"
DOCKERFILE = TOOLS / "yt-dlp" / "Dockerfile"

MEDIA_DIR = "/mnt/media/exercise"
MEDIA_UID = "1000:1000"            # PUID:PGID of the media stack (stacks/media/compose.yaml)
DEFAULT_URL = "https://jellyfin.kmkdp.com"
LINK_BASE = "https://jellyfin.kmkdp.com"   # what the wger Video: line points at (LAN + tunnel)
LIBRARY = "Exercise"
COLLECTION_TYPE = "homevideos"
TIMEOUT = 30
SCAN_WAIT = 600                    # seconds to wait for a scan to show every plan file
POLL = 5

YT_RE = re.compile(r"^https://www\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})$")
KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FILE_RE = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*) \[([A-Za-z0-9_-]{11})\]\.mp4$")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
FROM_RE = re.compile(r"^FROM\s+(\S+@sha256:[0-9a-f]{64})\s*$", re.M)
DAY_TAGS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# H.264 video + AAC audio (merged into mp4), else one progressive H.264/AAC stream; <= 1080p.
FORMAT = "bv*[vcodec^=avc1][height<=1080]+ba[acodec^=mp4a]/b[vcodec^=avc1][height<=1080][acodec^=mp4a]"
INCOMING = ".incoming"             # yt-dlp's temp dir inside the library dir (Jellyfin skips dot dirs)
# YouTube answers an intermittent "HTTP Error 403: Forbidden" on the video data; the same video
# usually works on the next try. Each video gets ATTEMPTS container runs, RETRY_PAUSE * n seconds
# after failed attempt n; a failed video never stops the run. BETWEEN: pause between videos.
ATTEMPTS = 4
RETRY_PAUSE = 30
BETWEEN = 5
# yt-dlp's own retries (README "Download Options"): keep its default counts (--retries 10,
# --fragment-retries 10) but back off between them instead of retrying at once.
YTDLP_RETRY = ["--extractor-retries", "5", "--retry-sleep", "http:exp=1:30",
               "--retry-sleep", "fragment:exp=1:30", "--retry-sleep", "extractor:5"]
HEADER = ("Written by exercise_videos.py jellyfin --apply; do not edit by hand. Plan key -> Jellyfin "
          "item id (gen_wger.py builds the Video: line from it) and the playlists the tool manages.")


class VideoError(RuntimeError):
    pass


# ---------------------------------------------------------------- the plan
def load_plan(path: Path = PLAN) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    errs = []
    for key, ex in (plan.get("exercises") or {}).items():
        if not KEY_RE.match(key):
            errs.append(f"exercises.{key}: key must be lower-case words joined by '-' (it is the file name)")
        if not YT_RE.match(ex.get("video") or ""):
            errs.append(f"exercises.{key}: video must be https://www.youtube.com/watch?v=<id>")
    if not plan.get("exercises"):
        errs.append("no exercises")
    if errs:
        raise VideoError(f"{path.name}:\n  " + "\n  ".join(errs))
    return plan


def youtube_id(ex: dict) -> str:
    return YT_RE.match(ex["video"]).group(1)


def file_name(key: str, ex: dict) -> str:
    """The stable file name: plan key + YouTube id, never the video title."""
    return f"{key} [{youtube_id(ex)}].mp4"


def wanted_files(plan: dict) -> dict[str, str]:
    """file name -> plan key."""
    return {file_name(k, ex): k for k, ex in plan["exercises"].items()}


def routine_keys(person: dict, routine: dict) -> list[str]:
    """The routine's exercise keys in workout order, parts expanded, each once."""
    out = []
    for s in routine["slots"]:
        slots = person.get("parts", {}).get(s["part"], []) if "part" in s else [s]
        for x in slots:
            if x["exercise"] not in out:
                out.append(x["exercise"])
    return out


def plan_playlists(plan: dict) -> list[dict]:
    """[{login, person, name, days: [tags], keys: [...]}]: one per person and distinct video list,
    in weekday order of the first day using it."""
    out = []
    for login, person in plan["people"].items():
        groups: dict[tuple, dict] = {}
        for r in sorted(person["routines"], key=lambda r: DAY_TAGS.index(r["day"])):
            keys = tuple(routine_keys(person, r))
            label = r["name"].split(" · ", 1)[1] if " · " in r["name"] else r["name"]
            g = groups.setdefault(keys, {"login": login, "person": person.get("name") or login,
                                         "days": [], "labels": [], "keys": list(keys)})
            g["days"].append(r["day"])
            if label not in g["labels"]:
                g["labels"].append(label)
        for g in groups.values():
            days = "/".join(DAY_NAMES[DAY_TAGS.index(d)] for d in g["days"])
            g["name"] = f"{g['person']} · {days} {' / '.join(g.pop('labels'))}"
            out.append(g)
    return out


# ---------------------------------------------------------------- download
def image_ref(path: Path = DOCKERFILE) -> str:
    m = FROM_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        raise VideoError(f"{path}: no 'FROM <image>:<tag>@sha256:<digest>' line")
    return m.group(1)


class Shell:
    """Runs commands locally or on --host over ssh (agent socket; the tool never handles keys)."""

    def __init__(self, host: str | None, run=subprocess.run):
        self.host = host
        self._run = run

    def __call__(self, argv: list[str], check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["ssh", "-o", "BatchMode=yes", self.host, shlex.join(argv)] if self.host else argv
        p = self._run(cmd, capture_output=True, text=True)
        if check and p.returncode != 0:
            raise VideoError(f"{shlex.join(argv)} failed ({p.returncode}): {(p.stderr or p.stdout).strip()[-400:]}")
        return p


def list_dir(sh: Shell, directory: str) -> list[str] | None:
    """Regular files directly in the directory, or None when it does not exist."""
    p = sh(["find", directory, "-mindepth", "1", "-maxdepth", "1", "-type", "f", "-printf", "%f\\n"], check=False)
    if p.returncode != 0:
        if sh(["test", "-d", directory], check=False).returncode != 0:
            return None
        raise VideoError(f"cannot list {directory}: {(p.stderr or '').strip()}")
    return sorted(x for x in p.stdout.splitlines() if x)


def ytdlp_argv(key: str, ex: dict) -> list[str]:
    """yt-dlp arguments for one plan exercise (output relative to /downloads)."""
    return ["--no-playlist", "--no-progress", "--no-mtime", "--no-cache-dir", "--no-overwrites",
            *YTDLP_RETRY, "-f", FORMAT, "--merge-output-format", "mp4",
            "--replace-in-metadata", "title", "(?s).+", ex["name"].replace("\\", "\\\\"),
            "--parse-metadata", "%(webpage_url)s:%(meta_comment)s", "--embed-metadata",
            "-P", "/downloads", "-P", f"temp:/downloads/{INCOMING}",
            "-o", f"{key} [%(id)s].%(ext)s", ex["video"]]


def docker_argv(docker: list[str], image: str, directory: str, key: str, ex: dict) -> list[str]:
    return [*docker, "run", "--rm", "--network", "bridge", "--user", MEDIA_UID,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--read-only",
            "--tmpfs", "/tmp", "-e", "HOME=/tmp", "--pids-limit", "512", "--memory", "2g",
            "-v", f"{directory}:/downloads", image, *ytdlp_argv(key, ex)]


def fetch(sh: Shell, argv: list[str], name: str, out, sleep) -> bool:
    """Run one video's container up to ATTEMPTS times; True when an attempt exited 0."""
    for n in range(1, ATTEMPTS + 1):
        p = sh(argv, check=False)
        if p.returncode == 0:
            return True
        lines = [x for x in (p.stderr or p.stdout or "").strip().splitlines() if x.strip()]
        why = lines[-1][-300:] if lines else f"exit {p.returncode}"
        if n == ATTEMPTS:
            out(f"FAILED {name} after {ATTEMPTS} attempts: {why}")
            return False
        out(f"retry {name} ({n}/{ATTEMPTS} failed: {why}); waiting {RETRY_PAUSE * n}s")
        sleep(RETRY_PAUSE * n)
    return False


def clean_incoming(sh: Shell, directory: str, apply: bool, out) -> None:
    """Remove the tool's own yt-dlp temp dir (<dir>/.incoming) and nothing else: only that one
    path, only a real directory (not a symlink), only when it holds no finished plan video."""
    tmp = str(PurePosixPath(directory) / INCOMING)
    link = sh(["test", "-L", tmp], check=False).returncode == 0
    if not link and sh(["test", "-e", tmp], check=False).returncode != 0:
        return
    if link or sh(["test", "-d", tmp], check=False).returncode != 0:
        out(f"WARN {INCOMING} is not a plain directory; left alone")
        return
    p = sh(["find", tmp, "-mindepth", "1", "-printf", "%P\\n"], check=False)
    if p.returncode != 0:
        out(f"WARN cannot list {INCOMING}/; left alone: {(p.stderr or '').strip()[-200:]}")
        return
    left = [x for x in p.stdout.splitlines() if x]
    done = [x for x in left if FILE_RE.match(PurePosixPath(x).name)]
    if done:
        out(f"WARN {INCOMING}/ holds finished video(s), left alone: " + ", ".join(done))
        return
    if not apply:
        out(f"{INCOMING}/ (yt-dlp temp dir, {len(left)} leftover file(s)): --apply removes it")
        return
    r = sh(["rm", "-rf", "--", tmp], check=False)
    if r.returncode != 0:
        out(f"WARN could not remove {INCOMING}/: {(r.stderr or '').strip()[-200:]}")
    else:
        out(f"removed {INCOMING}/ (yt-dlp temp dir, {len(left)} leftover file(s))")


def cmd_download(args, plan: dict, sh: Shell, out, sleep=time.sleep) -> int:
    directory = args.dir
    want = wanted_files(plan)
    have = list_dir(sh, directory)
    where = f"{args.host}:{directory}" if args.host else directory
    existed = have is not None
    if have is None:
        out(f"{where} does not exist yet" + (" (created by --apply)" if not args.apply else ""))
        have = []
    ours = [f for f in have if FILE_RE.match(f)]
    foreign = [f for f in have if not FILE_RE.match(f) and f != INCOMING]   # the temp dir is ours
    missing = [f for f in want if f not in have]
    extra = [f for f in ours if f not in want]
    out(f"plan: {len(want)} video(s); on the share: {len(set(want) & set(have))} present, "
        f"{len(missing)} missing, {len(extra)} no longer in the plan, {len(foreign)} other file(s)")
    for f in foreign:
        out(f"OTHER {f} (not a plan file name; never touched)")
    image = image_ref()
    docker = shlex.split(args.docker)
    if missing and args.apply:
        sh(["mkdir", "-p", directory])          # as the login user (uid 1000 = the share's owner)
    failed = []
    try:
        for i, f in enumerate(missing):
            key = want[f]
            if not args.apply:
                out(f"would download {f} <- {plan['exercises'][key]['video']}")
                continue
            if i:
                sleep(BETWEEN)
            out(f"download {f} <- {plan['exercises'][key]['video']} ({i + 1}/{len(missing)})")
            if not fetch(sh, docker_argv(docker, image, directory, key, plan["exercises"][key]), f, out, sleep):
                failed.append(f)
    finally:
        if existed or args.apply:
            clean_incoming(sh, directory, args.apply, out)
    for f in extra:
        if args.prune and args.apply:
            path = str(PurePosixPath(directory) / f)
            sh(["rm", "-f", "--", path])
            out(f"deleted {f} (no longer in the plan)")
        else:
            out(f"EXTRA {f}: no longer in the plan" + ("; would delete" if args.prune else "; --prune deletes it"))
    if args.apply and missing:
        after = list_dir(sh, directory) or []
        still = [f for f in missing if f not in after]
        if still:
            out(f"ERROR {len(still)} of {len(missing)} video(s) not created (re-run to fetch only these):")
            for f in still:
                out(f"  {f} <- {plan['exercises'][want[f]]['video']}"
                    + ("" if f in failed else " (yt-dlp exited 0 but the file is not there)"))
            return 1
    if not args.apply:
        out(f"dry run: {len(missing)} download(s), {len(extra) if args.prune else 0} delete(s) planned; add --apply")
    else:
        out("next: exercise_videos.py jellyfin --apply")
    return 0


# ---------------------------------------------------------------- Jellyfin
def norm(guid: str | None) -> str:
    return (guid or "").replace("-", "").lower()


class Jellyfin:
    """Thin client; in a dry run writes are recorded, not sent."""

    def __init__(self, base: str, key: str, apply: bool, actions: list):
        import requests                       # only the jellyfin step needs it
        self.base = base.rstrip("/")
        self.apply = apply
        self.actions = actions
        self.s = requests.Session()
        # Jellyfin 12 accepts only the MediaBrowser scheme (X-Emby-Token is gone)
        self.s.headers.update({"Authorization": f'MediaBrowser Token="{key}"', "Accept": "application/json"})

    def req(self, method: str, path: str, **kw):
        r = self.s.request(method, self.base + path, timeout=TIMEOUT, **kw)
        if r.status_code >= 400:
            raise VideoError(f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json() if r.content else None

    def get(self, path: str, **params):
        return self.req("GET", path, params=params)

    def write(self, method: str, path: str, what: str, body=None, params=None):
        self.actions.append(what)
        if not self.apply:
            return None
        return self.req(method, path, json=body, params=params)


def find_users(jf: Jellyfin, logins: list[str]) -> tuple[dict, list[dict]]:
    users = jf.get("/Users") or []
    by_name = {u["Name"].lower(): u for u in users}
    missing = [x for x in logins if x.lower() not in by_name]
    if missing:
        raise VideoError(f"no Jellyfin user named {missing} (the plan's people logins)")
    return {x: by_name[x.lower()] for x in logins}, users


def find_library(jf: Jellyfin, name: str) -> dict:
    libs = [v for v in jf.get("/Library/VirtualFolders") or [] if v.get("Name") == name]
    if len(libs) != 1:
        raise VideoError(f"Jellyfin has {len(libs)} libraries named {name!r}; create it first "
                         "(docs/runbooks/exercise-videos.md step 4)")
    lib = libs[0]
    if (lib.get("CollectionType") or "").lower() != COLLECTION_TYPE:
        raise VideoError(f"library {name!r} is {lib.get('CollectionType')!r}; it must be Home videos")
    if len(lib.get("Locations") or []) != 1:
        raise VideoError(f"library {name!r} must have exactly one folder, has {lib.get('Locations')}")
    return lib


def access_problems(users: list[dict], allowed: dict, lib_id: str) -> list[str]:
    """Who can see the library that should not, and who should but cannot."""
    probs = []
    ok_ids = {norm(u["Id"]) for u in allowed.values()}
    for u in users:
        pol = u.get("Policy") or {}
        sees = bool(pol.get("EnableAllFolders")) or lib_id in {norm(x) for x in pol.get("EnabledFolders") or []}
        if norm(u["Id"]) in ok_ids and not sees:
            probs.append(f"{u['Name']} cannot see the library (tick it under the user's Library access)")
        elif norm(u["Id"]) not in ok_ids and sees:
            why = "'Enable access to all libraries' is on" if pol.get("EnableAllFolders") else "the library is ticked"
            probs.append(f"{u['Name']} can see the library ({why}); it must be the plan's people only")
    return probs


def library_items(jf: Jellyfin, lib_id: str, location: str) -> dict[str, str]:
    """file name -> item id for the videos directly in the library folder."""
    res = jf.get("/Items", ParentId=lib_id, Recursive="true", IncludeItemTypes="Video", Fields="Path",
                 EnableImages="false", EnableUserData="false", Limit="5000")
    out = {}
    for it in (res or {}).get("Items", []):
        p = PurePosixPath(it.get("Path") or "")
        if str(p.parent) == location.rstrip("/"):
            out[p.name] = norm(it["Id"])
    return out


def scan(jf: Jellyfin, lib_id: str, location: str, want: list[str], wait: int, sleep=time.sleep) -> dict:
    """Start a scan of this library only, then poll until every wanted file is an item."""
    jf.write("POST", f"/Items/{lib_id}/Refresh", f"scan library {LIBRARY}",
             params={"Recursive": "true", "MetadataRefreshMode": "Default", "ImageRefreshMode": "Default",
                     "ReplaceAllMetadata": "false", "ReplaceAllImages": "false"})
    polls = max(1, wait // POLL)
    for n in range(polls + 1):
        items = library_items(jf, lib_id, location)
        if not jf.apply or all(f in items for f in want) or n == polls:
            return items
        sleep(POLL)
    return items


def user_playlists(jf: Jellyfin, uid: str) -> list[dict]:
    res = jf.get("/Items", userId=uid, IncludeItemTypes="Playlist", Recursive="true", Fields="Path")
    return (res or {}).get("Items", [])


def playlist_state(jf: Jellyfin, pid: str, uid: str) -> dict:
    info = jf.get(f"/Playlists/{pid}") or {}
    items = jf.get(f"/Playlists/{pid}/Items", userId=uid) or {}
    return {"public": bool(info.get("OpenAccess")), "shares": info.get("Shares") or [],
            "ids": [norm(i["Id"]) for i in items.get("Items", [])]}


def sync_playlists(jf: Jellyfin, plan_pls: list[dict], people: dict, item_of: dict, old: list[dict],
                   prune: bool, out) -> list[dict]:
    """Create/update each plan playlist; returns the managed list for wger_videos.json.

    A plan playlist is matched to the one recorded for that person and days in wger_videos.json
    (if the person still has it), else to the one playlist of the person with exactly its name,
    else created. Only recorded playlists are ever deleted (--prune), never one a person made."""
    managed = []
    for pl in plan_pls:
        uid = people[pl["login"]]["Id"]
        ids = [item_of[k] for k in pl["keys"]]
        mine = {norm(p["Id"]): p for p in user_playlists(jf, uid)}
        rec = [p["id"] for p in old if p["login"] == pl["login"] and p["days"] == pl["days"] and p["id"] in mine]
        named = [i for i, p in mine.items() if p.get("Name") == pl["name"]]
        pid = rec[0] if rec else (named[0] if len(named) == 1 else None)
        body = {"Name": pl["name"], "Ids": ids, "IsPublic": False, "Users": []}
        if pid is None:
            res = jf.write("POST", "/Playlists", f"create playlist {pl['name']!r} for {pl['login']} ({len(ids)} videos)",
                           {**body, "UserId": uid, "MediaType": "Video"})
            pid = norm((res or {}).get("Id")) or f"dry-{pl['login']}-{'-'.join(pl['days'])}"
        else:
            st = playlist_state(jf, pid, uid)
            diff = [w for w, bad in (("name", mine[pid].get("Name") != pl["name"]), ("order", st["ids"] != ids),
                                     ("public", st["public"]), ("shares", bool(st["shares"]))) if bad]
            if diff:
                jf.write("POST", f"/Playlists/{pid}", f"update playlist {pl['name']!r} ({', '.join(diff)})", body)
        if jf.apply:
            st = playlist_state(jf, pid, uid)
            if st["ids"] != ids or st["public"] or st["shares"]:
                raise VideoError(f"playlist {pl['name']!r} did not take: order/public/shares still differ")
        managed.append({"login": pl["login"], "days": pl["days"], "name": pl["name"], "id": pid})
    keep = {p["id"] for p in managed}
    for p in old:
        if p["id"] in keep:
            continue
        if prune:
            jf.write("DELETE", f"/Items/{p['id']}", f"delete playlist {p['name']!r} ({p['login']}): no longer in the plan")
        else:
            out(f"EXTRA playlist {p['name']!r} ({p['login']}) is no longer in the plan; --prune deletes it")
            managed.append(p)
    return managed


def load_videos(path: Path = VIDEOS) -> dict:
    if not path.exists():
        return {"items": {}, "playlists": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_videos(path: Path, items: dict, playlists: list, base: str) -> None:
    doc = {"_comment": HEADER, "base_url": base, "items": dict(sorted(items.items())),
           "playlists": sorted(playlists, key=lambda p: (p["login"], DAY_TAGS.index(p["days"][0])))}
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def cmd_jellyfin(args, plan: dict, out, sleep=time.sleep, videos_path: Path | None = None) -> int:
    videos_path = videos_path or VIDEOS
    want = wanted_files(plan)
    pls = plan_playlists(plan)
    key = os.environ.get("JELLYFIN_API_KEY")
    if not key:
        if args.apply:
            raise VideoError("JELLYFIN_API_KEY is not set (op read it into the env; never argv)")
        out("WARN JELLYFIN_API_KEY is not set: offline dry run (no server checks). Planned playlists:")
        for pl in pls:
            out(f"  {pl['name']} ({pl['login']}): " + ", ".join(pl["keys"]))
        out(f"{len(want)} file(s) expected in the library, {len(pls)} playlist(s)")
        return 0
    actions: list[str] = []
    jf = Jellyfin(os.environ.get("JELLYFIN_URL") or DEFAULT_URL, key, args.apply, actions)
    people, users = find_users(jf, list(plan["people"]))
    lib = find_library(jf, args.library)
    lib_id, location = norm(lib.get("ItemId")), lib["Locations"][0]
    probs = access_problems(users, people, lib_id)
    if probs:
        for p in probs:
            out(f"ACCESS {p}")
        raise VideoError("fix the library access first (runbook step 5); nothing written")
    opts = lib.get("LibraryOptions") or {}
    if opts and not opts.get("EnableEmbeddedTitles"):
        out("WARN library option 'Prefer embedded titles over filenames' is off: items show the file name")
    items = scan(jf, lib_id, location, list(want), args.wait, sleep)
    missing = [f for f in want if f not in items]
    if missing:
        for f in missing:
            out(f"MISSING {f}: not an item in {args.library!r} ({location})")
        if args.apply:
            raise VideoError(f"{len(missing)} plan file(s) not in the library; run download --apply, then retry")
        out("dry run: the playlists below skip missing videos")
    item_of = {want[f]: items[f] for f in want if f in items}
    old = load_videos(videos_path)
    ready = [pl for pl in pls if all(k in item_of for k in pl["keys"])]
    for pl in pls:
        if pl not in ready:
            out(f"SKIP playlist {pl['name']!r}: missing video(s)")
    managed = sync_playlists(jf, ready, people, item_of, old.get("playlists") or [], args.prune, out)
    mapping = {k: {"id": item_of[k], "youtube": youtube_id(plan["exercises"][k]), "file": file_name(k, plan["exercises"][k])}
               for k in item_of}
    changed = mapping != old.get("items") or managed != old.get("playlists") or old.get("base_url") != LINK_BASE
    for a in actions:
        out(f"{'did' if args.apply else 'would'}: {a}")
    if args.apply:
        if changed:
            write_videos(videos_path, mapping, managed, LINK_BASE)
            out(f"wrote {videos_path.name}: {len(mapping)} item id(s), {len(managed)} playlist(s); commit it, "
                "then gen_wger.py --apply")
        else:
            out(f"{videos_path.name} unchanged")
    else:
        out(f"{len(actions)} write(s) planned (dry run; add --apply)"
            + (f"; {videos_path.name} would change" if changed else ""))
    return 0


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None, plan_path: Path = PLAN, shell: Shell | None = None,
         sleep=time.sleep, videos_path: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="yt-dlp the plan videos onto the media share")
    d.add_argument("--dir", default=MEDIA_DIR, help=f"library directory on the media host (default {MEDIA_DIR})")
    d.add_argument("--host", help="run on this ssh host (e.g. kdkadmin@10.1.20.20) instead of locally")
    d.add_argument("--docker", default="sudo -n docker", help="docker command (default 'sudo -n docker')")
    j = sub.add_parser("jellyfin", help="scan the library, sync the playlists, write wger_videos.json")
    j.add_argument("--library", default=LIBRARY)
    j.add_argument("--wait", type=int, default=SCAN_WAIT, help="seconds to wait for the scan (default 600)")
    for p in (d, j):
        p.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
        p.add_argument("--prune", action="store_true", help="also delete what left the plan (with --apply)")
    args = ap.parse_args(argv)
    try:
        plan = load_plan(plan_path)
        if args.cmd == "download":
            return cmd_download(args, plan, shell or Shell(args.host), print, sleep)
        return cmd_jellyfin(args, plan, print, sleep, videos_path)
    except VideoError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
