#!/usr/bin/env python3
"""Plan videos as files in a Jellyfin "Exercise" library, plus one private playlist per person and day.

    # on kdk-dkr-01 (the media share is rw there), or from the laptop with --host:
    python3 exercise_videos.py download                       # dry run: what is missing / extra
    python3 exercise_videos.py download --apply               # yt-dlp the missing files
    python3 exercise_videos.py download --apply --prune       # ... and delete files no longer in the plan
    python3 exercise_videos.py download --apply --refetch goblet-squat   # replace one file in place
    python3 exercise_videos.py download --probe               # each file's resolution (read-only)
    python3 exercise_videos.py download --host kdkadmin@10.1.20.20 --apply

    # anywhere that reaches Jellyfin (key from the environment only):
    JELLYFIN_API_KEY=... python3 exercise_videos.py jellyfin            # dry run
    JELLYFIN_API_KEY=... python3 exercise_videos.py jellyfin --apply    # scan, playlists, wger_videos.json
    ... jellyfin --apply --prune                              # also delete playlists it made that left the plan

The source is wger_plan.json (each exercise's YouTube `video`; "video": null = no video by
design, e.g. treadmill-walk: no file, not in any playlist or the mapping). Runbook:
docs/runbooks/exercise-videos.md.

download: one flat directory (default /mnt/media/exercise), one file per plan exercise named
"<plan key> [<youtube id>].mp4". The name never depends on the YouTube title, and a file is
never renamed: Jellyfin derives the item id from the path, so a changed name would break the
wger links. A replaced video has a new YouTube id, so it is a new file (the old one is listed
as extra, deleted only with --prune). yt-dlp runs as a one-shot container (image pinned in
yt-dlp/Dockerfile) as uid:gid 1000:1000 (the media stack's PUID/PGID): H.264 + AAC in mp4, the
largest up to 1080 on the SHORT side (1920x1080 landscape, 1080x1920 portrait), the plan's
exercise name as the embedded title. --refetch <key> downloads an existing file again into the
temp dir and moves it over the old one only once it is complete (same path, so the Jellyfin item
id stays). --probe reports each file's resolution (ffprobe in the same image, no network, share
read-only). --prune only ever deletes files
whose name matches the pattern above; anything else in the directory is reported, not touched.
A failed video (YouTube's intermittent HTTP 403) is retried (ATTEMPTS runs, growing pause) and
never stops the run: the rest still download, then every video still missing is listed and the
exit code is 1. Afterwards the tool removes its own temp dir <dir>/.incoming (that path only,
and only when it holds no finished video).

jellyfin: finds the library by name (default "Exercise", type Home videos), refuses to go on
when one of the plan's people cannot see it (other accounts that can see it are only named in
a NOTE line: owner decision 2026-11-14, the library is not private), starts a scan of that library only
and waits until every plan file is an item, then creates or updates one playlist per person
and day: "<name> · <n> <day> <label>" (e.g. "Jacob · 1 Mon Lower A"; n = position in that
person's week, so Jellyfin's name sort shows week order), items in workout order, owned
by that person, not public, no shares; a wrong one is deleted and created again (an API key
cannot update or rename a playlist in place), and each one is verified as every other account (it must
not see it). Days with the same video list share one playlist
("Rachel · 3 Wed/Sat/Sun Daily parts"). A playlist with the pre-2026-11-14 name (no number, e.g.
"Jacob · Mon Lower A") is replaced: the new one is created and verified, then the old one is
deleted (only that person's own, exact generated name; a failed delete exits 1). Writes wger_videos.json (plan key -> item id, plus the
playlist ids it manages and verified) so gen_wger.py can build the Video: line without a
Jellyfin key; a run that stops at a playlist still writes the item ids, and the next run resumes.
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
# H.264 video + AAC audio (merged into mp4), else one progressive H.264/AAC stream. The size cap
# is the sort, not a filter: yt-dlp's `res` is the SHORT side, so -S res:1080 takes the largest
# stream up to 1080 on the short side for landscape (1920x1080) and portrait (1080x1920) alike
# (README "Sorting Formats"). The old [height<=1080] filter dropped every portrait stream taller
# than 1080, so portrait videos came down at 480x854.
FORMAT = "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1][acodec^=mp4a]"
SORT = "res:1080"
MIN_SHORT = 1080                   # download --probe flags a file whose short side is below this
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
        if "video" in ex and ex["video"] is None:
            continue                          # no video by design: no file, no playlist entry
        if not YT_RE.match(str(ex.get("video") or "")):
            errs.append(f"exercises.{key}: video must be https://www.youtube.com/watch?v=<id> (or null for none)")
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


def has_video(ex: dict) -> bool:
    """False for an exercise with "video": null (no video by design, e.g. treadmill-walk)."""
    return ex.get("video") is not None


def wanted_files(plan: dict) -> dict[str, str]:
    """file name -> plan key, for every exercise that has a video."""
    return {file_name(k, ex): k for k, ex in plan["exercises"].items() if has_video(ex)}


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
    """[{login, person, name, old_name, days: [tags], keys: [...]}]: one per person and distinct
    video list, in weekday order of the first day using it.

    name = "<person> · <n> <days> <labels>": n is the playlist's position in that person's week
    (1, 2, ... by first day; zero-padded when a person has 10 or more), so Jellyfin's plain name
    sort lists them in week order. old_name is the scheme before 2026-11-14 (no number); the
    jellyfin step replaces playlists that still carry it."""
    out = []
    for login, person in plan["people"].items():
        groups: dict[tuple, dict] = {}
        for r in sorted(person["routines"], key=lambda r: DAY_TAGS.index(r["day"])):
            keys = tuple(k for k in routine_keys(person, r) if has_video(plan["exercises"][k]))
            if not keys:                      # a day of no-video exercises only: no playlist
                continue
            label = r["name"].split(" · ", 1)[1] if " · " in r["name"] else r["name"]
            g = groups.setdefault(keys, {"login": login, "person": person.get("name") or login,
                                         "days": [], "labels": [], "keys": list(keys)})
            g["days"].append(r["day"])
            if label not in g["labels"]:
                g["labels"].append(label)
        width = len(str(len(groups)))
        for n, g in enumerate(groups.values(), 1):
            rest = "/".join(DAY_NAMES[DAY_TAGS.index(d)] for d in g["days"]) + " " + " / ".join(g.pop("labels"))
            g["name"] = f"{g['person']} · {n:0{width}d} {rest}"
            g["old_name"] = f"{g['person']} · {rest}"
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


def ytdlp_argv(key: str, ex: dict, refetch: bool = False) -> list[str]:
    """yt-dlp arguments for one plan exercise (output relative to /downloads). A refetch puts the
    finished file in the temp dir (/downloads/.incoming) instead; the tool then moves it over the
    old one, so the old file stays in place until the new one is complete."""
    home = f"/downloads/{INCOMING}" if refetch else "/downloads"
    return ["--no-playlist", "--no-progress", "--no-mtime", "--no-cache-dir", "--no-overwrites",
            *YTDLP_RETRY, "-f", FORMAT, "-S", SORT, "--merge-output-format", "mp4",
            "--replace-in-metadata", "title", "(?s).+", ex["name"].replace("\\", "\\\\"),
            "--parse-metadata", "%(webpage_url)s:%(meta_comment)s", "--embed-metadata",
            "-P", home, "-P", f"temp:/downloads/{INCOMING}",
            "-o", f"{key} [%(id)s].%(ext)s", ex["video"]]


def container(docker: list[str], network: str, mount: str) -> list[str]:
    """`docker run` with the hardening every one-shot container here gets, up to the image."""
    return [*docker, "run", "--rm", "--network", network, "--user", MEDIA_UID,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--read-only",
            "--tmpfs", "/tmp", "-e", "HOME=/tmp", "--pids-limit", "512", "--memory", "2g", "-v", mount]


def docker_argv(docker: list[str], image: str, directory: str, key: str, ex: dict,
                refetch: bool = False) -> list[str]:
    return [*container(docker, "bridge", f"{directory}:/downloads"), image, *ytdlp_argv(key, ex, refetch)]


# One container, no network, the share mounted read-only: the first video stream's size per mp4.
PROBE_SH = ('cd /downloads && for f in *.mp4; do [ -f "$f" ] || continue; printf \'%s\\t%s\\n\' '
            '"$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0:s=x "./$f")" '
            '"$f"; done')
SIZE_RE = re.compile(r"^(\d+)x(\d+)")


def probe_argv(docker: list[str], image: str, directory: str) -> list[str]:
    return [*container(docker, "none", f"{directory}:/downloads:ro"), "--entrypoint", "sh", image, "-c", PROBE_SH]


def cmd_probe(args, plan: dict, sh: Shell, out) -> int:
    """download --probe: every plan file's resolution, the ones below MIN_SHORT on the short side
    flagged LOW. Read-only and offline; whether YouTube has better for a LOW one is the runbook's
    format check (some videos only exist at 720p)."""
    want = wanted_files(plan)
    p = sh(probe_argv(shlex.split(args.docker), image_ref(), args.dir))
    sizes = {}
    for line in p.stdout.splitlines():
        size, _, name = line.partition("\t")
        m = SIZE_RE.match(size.strip())
        sizes[name] = (int(m.group(1)), int(m.group(2))) if m else None
    low = 0
    for f in sorted(want):
        url = plan["exercises"][want[f]]["video"]
        if f not in sizes:
            out(f"MISSING {f}")
        elif sizes[f] is None:
            low += 1
            out(f"LOW ? (ffprobe found no video stream) {f} <- {url}")
        elif min(sizes[f]) < MIN_SHORT:
            low += 1
            out(f"LOW {sizes[f][0]}x{sizes[f][1]} {f} <- {url}")
        else:
            out(f"ok  {sizes[f][0]}x{sizes[f][1]} {f}")
    out(f"probe: {len(want)} plan video(s), {low} below {MIN_SHORT} on the short side (LOW). For each LOW one "
        "check what YouTube has (runbook, Troubleshooting: low resolution), then download --refetch <key> "
        "or replace the video in the plan")
    return 0


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


def replace(sh: Shell, argv: list[str], directory: str, name: str, out, sleep) -> bool:
    """--refetch: download into <dir>/.incoming/<name>, then mv it over <dir>/<name>. The old file
    is untouched until the new one is complete (yt-dlp exited 0, the file is there and not empty);
    the path stays the same, so the Jellyfin item id and the wger links stay too."""
    new = str(PurePosixPath(directory) / INCOMING / name)
    if sh(["test", "-e", new], check=False).returncode == 0 or sh(["test", "-L", new], check=False).returncode == 0:
        out(f"FAILED {name}: {INCOMING}/{name} already exists (an earlier refetch that was not moved?); "
            "check it, delete it, run again")
        return False
    if not fetch(sh, argv, name, out, sleep):
        return False
    if sh(["test", "-s", new], check=False).returncode != 0:
        out(f"FAILED {name}: yt-dlp exited 0 but {INCOMING}/{name} is not there; the old file is kept")
        return False
    p = sh(["mv", "-f", "--", new, str(PurePosixPath(directory) / name)], check=False)
    if p.returncode != 0:
        out(f"FAILED {name}: could not move the new file into place ({(p.stderr or '').strip()[-200:]}); "
            f"the old file is kept, the new one is in {INCOMING}/")
        return False
    out(f"replaced {name}")
    return True


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
    by_key = {k: f for f, k in want.items()}
    refetch = []
    for key in dict.fromkeys(args.refetch or []):
        if key not in by_key:
            raise VideoError(f"--refetch {key}: not a plan exercise key with a video")
        if by_key[key] in missing:
            out(f"NOTE --refetch {key}: {by_key[key]} is not on the share yet; it is a normal download")
        else:
            refetch.append(by_key[key])
    jobs = [(f, False) for f in missing] + [(f, True) for f in refetch]
    image = image_ref()
    docker = shlex.split(args.docker)
    if missing and args.apply:
        sh(["mkdir", "-p", directory])          # as the login user (uid 1000 = the share's owner)
    failed = []
    try:
        for i, (f, again) in enumerate(jobs):
            key = want[f]
            url = plan["exercises"][key]["video"]
            if not args.apply:
                out(f"would {'replace' if again else 'download'} {f} <- {url}"
                    + (" (re-download into .incoming/, then move over the existing file)" if again else ""))
                continue
            if i:
                sleep(BETWEEN)
            out(f"{'refetch' if again else 'download'} {f} <- {url} ({i + 1}/{len(jobs)})")
            if not again:
                if not fetch(sh, docker_argv(docker, image, directory, key, plan["exercises"][key]), f, out, sleep):
                    failed.append(f)
            elif not replace(sh, docker_argv(docker, image, directory, key, plan["exercises"][key], refetch=True),
                             directory, f, out, sleep):
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
    rc = 0
    if args.apply and missing:
        after = list_dir(sh, directory) or []
        still = [f for f in missing if f not in after]
        if still:
            out(f"ERROR {len(still)} of {len(missing)} video(s) not created (re-run to fetch only these):")
            for f in still:
                out(f"  {f} <- {plan['exercises'][want[f]]['video']}"
                    + ("" if f in failed else " (yt-dlp exited 0 but the file is not there)"))
            rc = 1
    not_replaced = [f for f in refetch if f in failed]
    if args.apply and not_replaced:
        out(f"ERROR {len(not_replaced)} of {len(refetch)} refetch(es) failed; the old file is still in place:")
        for f in not_replaced:
            out(f"  --refetch {want[f]}")
        rc = 1
    if not args.apply:
        out(f"dry run: {len(missing)} download(s), {len(refetch)} replace(s), "
            f"{len(extra) if args.prune else 0} delete(s) planned; add --apply")
    elif rc == 0:
        out("next: exercise_videos.py jellyfin --apply")
    return rc


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

    def status(self, path: str, **params) -> int:
        """HTTP status of a GET, for checks where 404 is the expected answer."""
        return self.s.request("GET", self.base + path, timeout=TIMEOUT, params=params).status_code

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


def library_access(users: list[dict], allowed: dict, lib_id: str) -> tuple[list[str], list[str]]:
    """(problems, others): plan people who cannot see the library (a hard stop), and the other
    accounts who can (allowed since the owner decision of 2026-11-14; only reported)."""
    probs, others = [], []
    ok_ids = {norm(u["Id"]) for u in allowed.values()}
    for u in users:
        pol = u.get("Policy") or {}
        sees = bool(pol.get("EnableAllFolders")) or lib_id in {norm(x) for x in pol.get("EnabledFolders") or []}
        if norm(u["Id"]) in ok_ids and not sees:
            probs.append(f"{u['Name']} cannot see the library (tick it under the user's Library access)")
        elif norm(u["Id"]) not in ok_ids and sees:
            others.append(f"{u['Name']} ({'all libraries' if pol.get('EnableAllFolders') else 'ticked'})")
    return probs, others


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


# Jellyfin 12 with an API key (no user session): GET /Playlists/{id}, GET /Playlists/{id}/Users,
# POST /Playlists/{id} (update), DELETE /Playlists/{id}/Items and .../Move all use the calling
# user and answer 400. What works: POST /Playlists with UserId in the body, the user-scoped reads
# /Playlists/{id}/Items?userId=, /Items?userId=&IncludeItemTypes=Playlist, /Items/{id}?userId=,
# and DELETE /Items/{id}. So a playlist is never updated in place: a wrong one is deleted and
# created again, and privacy is checked from every other user's point of view.
def playlist_items(jf: Jellyfin, pid: str, uid: str) -> list[str]:
    """The playlist's item ids in order, as its owner sees them."""
    res = jf.get(f"/Playlists/{pid}/Items", userId=uid) or {}
    return [norm(i["Id"]) for i in res.get("Items", [])]


def who_else_sees(jf: Jellyfin, pid: str, owner: str, users: list[dict]) -> list[str]:
    """Every account other than the owner that can see the playlist (should be none): it is in
    that user's playlist list, or GET /Items/{id} as that user is not a 404."""
    seen = []
    for u in users:
        if norm(u["Id"]) == norm(owner):
            continue
        why = []
        if pid in {norm(p["Id"]) for p in user_playlists(jf, u["Id"])}:
            why.append("in their playlists")
        code = jf.status(f"/Items/{pid}", userId=u["Id"])
        if code != 404:
            why.append(f"GET /Items/{pid} as them: HTTP {code}")
        if why:
            seen.append(f"{u['Name']} ({'; '.join(why)})")
    return seen


def verify_playlist(jf: Jellyfin, pl: dict, pid: str, uid: str, ids: list[str], users: list[dict]) -> None:
    got = playlist_items(jf, pid, uid)
    if got != ids:
        raise VideoError(f"playlist {pl['name']!r} ({pid}): {len(got)} item(s) as {pl['login']} sees it, "
                         "not the plan's videos in workout order")
    others = who_else_sees(jf, pid, uid, users)
    if others:
        raise VideoError(f"playlist {pl['name']!r} ({pid}) is visible to {', '.join(others)}; it must be "
                         f"{pl['login']}'s only. As {pl['login']}, remove the shares / make it private (or delete "
                         "it: the next run creates it again)")


def delete_replaced(jf: Jellyfin, pl: dict, pid: str, name: str, why: str, problems: list[str], out) -> None:
    """DELETE /Items/{pid} for a replaced playlist; a failure is reported and the run goes on."""
    try:
        jf.write("DELETE", f"/Items/{pid}", f"delete playlist {name!r} ({pid}) of {pl['login']}: {why}")
    except VideoError as e:
        jf.actions[-1] += " -- FAILED"
        problems.append(f"could not delete playlist {name!r} ({pid}) of {pl['login']}: {e}. It is no longer "
                        "recorded; the next run finds it by its exact name and tries again (or delete it by hand)")
        out(f"ERROR {problems[-1]}")


def sync_playlists(jf: Jellyfin, plan_pls: list[dict], people: dict, users: list[dict], item_of: dict,
                   old: list[dict], prune: bool, out, managed: list[dict] | None = None,
                   problems: list[str] | None = None) -> list[dict]:
    """Create/re-create each plan playlist; returns the managed list for wger_videos.json.

    Only a playlist of the plan person with an exact generated name (pl["name"], or the
    pre-2026-11-14 pl["old_name"]) is ever adopted, replaced or deleted, and before a delete the
    tool checks that no other account can see it (so it is that person's own, not a share or a
    public playlist). Anything else, a hand-made playlist included, is never touched.

    A plan playlist is matched to the one recorded for that person and days in wger_videos.json
    (if the person still has it under its current name), else to the one playlist of the person
    with exactly its name, else created. A matched one with the wrong order is deleted and created
    again (an API key cannot update one in place). Every playlist of the person with exactly the
    old name (recorded or not) is deleted only after the new-named one is verified. A failed DELETE is
    appended to `problems` (the caller exits non-zero) and the run goes on. Every kept/created
    playlist is verified: the owner sees the plan's videos in order and no other account can see
    it at all; a failure stops the run. Only verified playlists are appended to `managed` (pass a
    list to keep them on a failure). --prune deletes only recorded playlists whose days left the plan."""
    managed = [] if managed is None else managed
    problems = [] if problems is None else problems
    in_plan = {(pl["login"], tuple(pl["days"])) for pl in plan_pls}
    for pl in plan_pls:
        uid = people[pl["login"]]["Id"]
        ids = [item_of[k] for k in pl["keys"]]
        mine = {norm(p["Id"]): p for p in user_playlists(jf, uid)}
        generated = (pl["name"], pl["old_name"])
        rec = []
        for p in old:
            if p["login"] == pl["login"] and p["days"] == pl["days"] and p["id"] in mine:
                if mine[p["id"]].get("Name") in generated:
                    rec.append(p["id"])
                else:
                    out(f"NOTE recorded playlist {p['id']} of {pl['login']} is now named "
                        f"{mine[p['id']].get('Name')!r} (renamed by hand?): left alone, no longer managed")
        rec = [i for i in rec if mine[i].get("Name") == pl["name"]]   # an old-named record is in named_old
        named = [i for i, p in mine.items() if p.get("Name") == pl["name"]]
        # old-named playlists (recorded or not) are deleted once the new-named one is verified
        replace = [i for i, p in mine.items() if p.get("Name") == pl["old_name"]]
        pid = rec[0] if rec else (named[0] if len(named) == 1 else None)
        if pid is not None and playlist_items(jf, pid, uid) != ids:
            others = who_else_sees(jf, pid, uid, users)
            if others:                        # not provably this person's own: never delete it
                raise VideoError(f"playlist {pl['name']!r} ({pid}) has the wrong order and is visible to "
                                 f"{', '.join(others)}; not deleted. As {pl['login']}, delete it by hand; "
                                 "the next run creates it again")
            jf.write("DELETE", f"/Items/{pid}", f"delete playlist {pl['name']!r} ({pid}) to re-create it (order differs)")
            pid = None
        if pid is None:
            body = {"Name": pl["name"], "Ids": ids, "UserId": uid, "MediaType": "Video", "IsPublic": False, "Users": []}
            res = jf.write("POST", "/Playlists", f"create playlist {pl['name']!r} for {pl['login']} ({len(ids)} videos)", body)
            pid = norm((res or {}).get("Id")) or None
            if pid is None and jf.apply:
                raise VideoError(f"create playlist {pl['name']!r}: the answer has no Id")
        if pid is None:                       # dry run: nothing created, nothing to verify
            pid = f"dry-{pl['login']}-{'-'.join(pl['days'])}"
        else:
            verify_playlist(jf, pl, pid, uid, ids, users)
        managed.append({"login": pl["login"], "days": pl["days"], "name": pl["name"], "id": pid})
        for rid in replace:                   # only after the new one is verified
            others = who_else_sees(jf, rid, uid, users)
            if others:
                problems.append(f"playlist {pl['old_name']!r} ({rid}) is visible to {', '.join(others)}, so it "
                                f"may not be {pl['login']}'s own; not deleted. Delete it by hand if it is")
                out(f"WARN {problems[-1]}")
                continue
            delete_replaced(jf, pl, rid, pl["old_name"], f"replaced by {pl['name']!r}", problems, out)
    keep = {p["id"] for p in managed}
    for p in old:
        if p["id"] in keep or (p["login"], tuple(p["days"])) in in_plan:
            continue                          # still in the plan: handled (kept, replaced or dropped) above
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
    probs, others = library_access(users, people, lib_id)
    if probs:
        for p in probs:
            out(f"ACCESS {p}")
        raise VideoError("fix the library access first (runbook step 4); nothing written")
    if others:
        out(f"NOTE other accounts can see the library (allowed; the playlists stay private): {', '.join(others)}")
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
    mapping = {k: {"id": item_of[k], "youtube": youtube_id(plan["exercises"][k]), "file": file_name(k, plan["exercises"][k])}
               for k in item_of}
    managed: list[dict] = []
    problems: list[str] = []
    try:
        sync_playlists(jf, ready, people, users, item_of, old.get("playlists") or [], args.prune, out, managed,
                       problems)
    except VideoError:
        # Every plan file is an item by now, so the item ids are good: write them (gen_wger.py needs
        # only those) with just the playlists verified so far. The next run resumes: it finds the
        # rest by name or creates them.
        for a in actions:
            out(f"{'did' if args.apply else 'would'}: {a}")
        if args.apply:
            write_videos(videos_path, mapping, managed, LINK_BASE)
            out(f"wrote {videos_path.name} before stopping: {len(mapping)} item id(s), {len(managed)} verified "
                "playlist(s); fix the error below and run again")
        raise
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
    if problems:
        out(f"ERROR {len(problems)} old playlist(s) not removed (the new ones are made and recorded):")
        for p in problems:
            out(f"  {p}")
        return 1
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
    d.add_argument("--refetch", action="append", metavar="KEY",
                   help="download this plan key again and replace its file in place (same name; repeatable)")
    d.add_argument("--probe", action="store_true",
                   help=f"only report each file's resolution (ffprobe, read-only); LOW = short side < {MIN_SHORT}")
    j = sub.add_parser("jellyfin", help="scan the library, sync the playlists, write wger_videos.json")
    j.add_argument("--library", default=LIBRARY)
    j.add_argument("--wait", type=int, default=SCAN_WAIT, help="seconds to wait for the scan (default 600)")
    for p in (d, j):
        p.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
        p.add_argument("--prune", action="store_true", help="also delete what left the plan (with --apply)")
    args = ap.parse_args(argv)
    try:
        plan = load_plan(plan_path)
        if args.cmd == "download" and args.probe:
            if args.apply or args.prune or args.refetch:
                raise VideoError("--probe only reads; it does not combine with --apply / --prune / --refetch")
            return cmd_probe(args, plan, shell or Shell(args.host), print)
        if args.cmd == "download":
            return cmd_download(args, plan, shell or Shell(args.host), print, sleep)
        return cmd_jellyfin(args, plan, print, sleep, videos_path)
    except VideoError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
