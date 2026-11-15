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

download also keeps the DAY FOLDERS (default /mnt/media/exercise-days, --days-dir): one folder per
playlist (same grouping, the playlist name made file-system safe: "Rachel · 2 Tue-Fri Cardio"),
holding HARDLINKS "<NN> <exercise name>.mp4" to the plan files, NN = position in the day's workout
order among the exercises with a video (01, 02, ...). Why: the Apple TV client (Neptune) can play
neither a playlist nor a folder, only a flat library sorted by name, so each day is its own
Jellyfin library (jellyfin step below). Same NFS filesystem, so no extra space. Every run
(after the downloads, --refetch and --prune) compares inodes: a missing link is made, a link to an
old inode (a refetch moves a new file over the old name, which breaks the hardlink) is made again,
a link the day no longer has is removed. Only ever inside --days-dir, only folder and file names
it generates (anything else there is reported, never touched, symlinks never followed); a folder of
a day that left the plan is emptied and removed only with --prune.

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

jellyfin then makes one DAY LIBRARY per day folder (owner decision 2026-11-14, "option B"): name =
the folder name, Home videos, folder /data/media/exercise-days/<name>, every metadata and image
fetcher off, "prefer embedded titles" OFF (the item name is the file name "01 Dumbbell Goblet
Squat", so the name sort is the workout order), no trickplay, chapter images or real-time
monitoring. Created with POST /Library/VirtualFolders; if the server refuses that for the API key,
the tool prints the hand steps and adopts the library on the next run (exact name, type and
folder). Each one is scanned and verified: its items sorted by name are exactly the day's files in
plan order. Access is left as it is (owner decision): a NOTE names who can see each library.
wger_videos.json records them ("libraries": name, id, origin api|hand); --prune deletes only a
library the tool created (recorded id AND exact generated name, origin api), never Exercise,
Movies, Shows or Collections.

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
          "item id (gen_wger.py builds the Video: line from it), the playlists and the day libraries "
          "the tool manages (a library is deleted only when recorded here with origin api).")

# Day folders / day libraries (owner decision 2026-11-14, option B: Neptune plays only a flat library)
DAYS_DIR = "/mnt/media/exercise-days"          # on the share (kdk-dkr-01 rw)
DAYS_IN_JELLYFIN = "/data/media/exercise-days"  # the same, inside the jellyfin container (ro)
LINK_RE = re.compile(r"^(\d{2,}) ([^/]+)\.mp4$")
UNSAFE_RE = re.compile(r'[\\:*?"<>|\[\]\x00-\x1f\x7f]')   # plus "/" -> "-" (Tue/Fri -> Tue-Fri)
DAY_LIB_RE = re.compile(r"^[^/]+ · \d+ [^/]+$")             # the shape of a generated day name
PROTECTED = {"exercise", "movies", "shows", "collections"}   # never deleted, whatever the record says
# Library options for a day library. The tool sends them on create and re-applies them when one
# drifted. EnableEmbeddedTitles OFF: the item name must be the file name ("01 ..."), not the
# embedded title (the plain exercise name), or the name sort loses the workout order.
DAY_OPTIONS = {
    "Enabled": True, "EnablePhotos": False, "EnableRealtimeMonitor": False, "EnableLUFSScan": False,
    "EnableChapterImageExtraction": False, "ExtractChapterImagesDuringLibraryScan": False,
    "EnableTrickplayImageExtraction": False, "ExtractTrickplayImagesDuringLibraryScan": False,
    "SaveTrickplayWithMedia": False, "SaveLocalMetadata": False, "EnableInternetProviders": False,
    "EnableEmbeddedTitles": False, "EnableEmbeddedExtrasTitles": False, "AutomaticRefreshIntervalDays": 0,
    "MetadataSavers": [],
    "TypeOptions": [{"Type": "Video", "MetadataFetchers": [], "MetadataFetcherOrder": [],
                     "ImageFetchers": [], "ImageFetcherOrder": [], "ImageOptions": []}],
}


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


def safe_name(s: str) -> str:
    """A file-system (and Jellyfin name parser) safe name: "/" -> "-", \\ : * ? " < > | [ ] and
    control characters -> space, whitespace collapsed, no leading/trailing dot or space (a dot
    file is hidden from Jellyfin)."""
    s = UNSAFE_RE.sub(" ", s.replace("/", "-"))
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s or len(s.encode()) > 200:
        raise VideoError(f"cannot make a safe file name from {s!r}")
    return s


def day_libraries(plan: dict) -> list[dict]:
    """One per plan playlist: {login, days, name (folder = library name), links: [{name, key, source}]}.
    Link name "<NN> <exercise name>.mp4", NN = workout position among the exercises with a video,
    at least two digits, so the plain name sort is the workout order."""
    out, seen = [], set()
    for pl in plan_playlists(plan):
        name = safe_name(pl["name"])
        if name in seen:
            raise VideoError(f"two day folders would both be named {name!r}")
        seen.add(name)
        width = max(2, len(str(len(pl["keys"]))))
        links = [{"name": f"{n:0{width}d} {safe_name(plan['exercises'][k]['name'])}.mp4", "key": k,
                  "source": file_name(k, plan["exercises"][k])} for n, k in enumerate(pl["keys"], 1)]
        out.append({"login": pl["login"], "days": pl["days"], "name": name, "links": links})
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


def list_inodes(sh: Shell, directory: str) -> dict[str, str] | None:
    """file name -> inode for the regular files directly in the directory (None: no directory)."""
    p = sh(["find", directory, "-mindepth", "1", "-maxdepth", "1", "-type", "f", "-printf", "%i\\t%f\\n"],
           check=False)
    if p.returncode != 0:
        return None
    return dict(reversed(x.split("\t", 1)) for x in p.stdout.splitlines() if "\t" in x)


def list_days(sh: Shell, days_dir: str) -> tuple[dict, dict] | None:
    """({top-level name: find type}, {folder: {file: (type, inode)}}) of the day folders dir, two
    levels, symlinks not followed (a symlinked folder is type "l" and never entered)."""
    p = sh(["find", days_dir, "-mindepth", "1", "-maxdepth", "2", "-printf", "%y\\t%i\\t%P\\n"], check=False)
    if p.returncode != 0:
        if sh(["test", "-d", days_dir], check=False).returncode != 0:
            return None
        raise VideoError(f"cannot list {days_dir}: {(p.stderr or '').strip()[-200:]}")
    top, files = {}, {}
    for line in p.stdout.splitlines():
        typ, ino, rel = (line.split("\t", 2) + ["", ""])[:3]
        if not rel:
            continue
        if "/" in rel:
            d, f = rel.split("/", 1)
            files.setdefault(d, {})[f] = (typ, ino)
        else:
            top[rel] = typ
    return top, files


def day_path(days_dir: str, *parts: str) -> str:
    """A path strictly inside days_dir, one plain name per part (the tool never writes elsewhere)."""
    for x in parts:
        if not x or "/" in x or x in (".", "..") or "\x00" in x:
            raise VideoError(f"refusing unsafe name {x!r} under {days_dir}")
    return str(PurePosixPath(days_dir, *parts))


def sync_days(sh: Shell, plan: dict, directory: str, days_dir: str, apply: bool, prune: bool, out) -> int:
    """Make the day folders match the plan (see the module docstring). Returns the number of
    problems (missing sources, things in the way); the caller exits 1 on any."""
    libs = day_libraries(plan)
    src = list_inodes(sh, directory) or {}
    listing = list_days(sh, days_dir)
    if listing is None:
        out(f"{days_dir} does not exist yet" + ("; creating it" if apply else " (created by --apply)"))
        if apply:
            sh(["mkdir", "-p", "--", days_dir])
        listing = ({}, {})
    top, files = listing
    planned = {lib["name"] for lib in libs}
    problems, waiting = 0, 0
    counts = {"ok": 0, "link": 0, "relink": 0, "remove": 0}
    verb = (lambda done, todo: done) if apply else (lambda done, todo: f"would {todo}")
    for lib in libs:
        name = lib["name"]
        if top.get(name, "d") != "d":
            out(f"WARN day folder {name!r} is not a plain directory (type {top[name]}); left alone")
            problems += 1
            continue
        have = files.get(name, {})
        if name not in top and apply:
            sh(["mkdir", "-p", "--", day_path(days_dir, name)])
        want = {x["name"]: x for x in lib["links"]}
        for lname, x in want.items():
            if x["source"] not in src:
                waiting += 1
                if apply:
                    out(f"SKIP {name}/{lname}: source {x['source']} is not on the share")
                continue
            cur = have.get(lname)
            if cur == ("f", src[x["source"]]):
                counts["ok"] += 1
                continue
            if cur is not None and cur[0] not in ("f", "l"):
                out(f"WARN {name}/{lname} is in the way (type {cur[0]}); left alone")
                problems += 1
                continue
            kind = "link" if cur is None else "relink"
            counts[kind] += 1
            why = "" if cur is None else (" (a symlink)" if cur[0] == "l" else " (the source was replaced: inode differs)")
            if apply:
                sh(["ln", "-fT", "--", str(PurePosixPath(directory) / x["source"]), day_path(days_dir, name, lname)])
            out(f"{verb(kind + 'ed', kind)} {name}/{lname} <- {x['source']}{why}")
        for fname, (typ, _ino) in sorted(have.items()):
            if fname in want:
                continue
            if LINK_RE.match(fname) and typ in ("f", "l"):
                counts["remove"] += 1
                if apply:
                    sh(["rm", "-f", "--", day_path(days_dir, name, fname)])
                out(f"{verb('removed', 'remove')} {name}/{fname} (not in this day any more)")
            else:
                out(f"OTHER {name}/{fname} (not a name the tool makes; never touched)")
    for d, typ in sorted(top.items()):
        if d in planned:
            continue
        if typ != "d" or not DAY_LIB_RE.match(d):
            out(f"OTHER {d} in {days_dir} (not a day folder the tool makes; never touched)")
            continue
        ours = sorted(f for f, (t, _) in files.get(d, {}).items() if LINK_RE.match(f) and t in ("f", "l"))
        rest = sorted(f for f in files.get(d, {}) if f not in ours)
        if not (prune and apply):
            out(f"EXTRA day folder {d!r} ({len(ours)} link(s)): no longer in the plan"
                + ("; would delete" if prune else "; --prune deletes it"))
            continue
        for f in ours:
            sh(["rm", "-f", "--", day_path(days_dir, d, f)])
        if rest:
            out(f"WARN day folder {d!r}: removed {len(ours)} link(s), kept (not ours): {', '.join(rest)}")
            problems += 1
            continue
        sh(["rmdir", "--", day_path(days_dir, d)])
        out(f"deleted day folder {d!r} ({len(ours)} link(s); no longer in the plan)")
    out(f"day folders: {len(libs)} in the plan; links {counts['ok']} ok, {counts['link']} new, "
        f"{counts['relink']} re-linked, {counts['remove']} removed{'' if apply else ' (planned)'}"
        + (f", {waiting} waiting for a source file" if waiting else ""))
    return problems + (waiting if apply else 0)


def cmd_download(args, plan: dict, sh: Shell, out, sleep=time.sleep) -> int:
    directory = args.dir
    days = PurePosixPath(args.days_dir)
    if not days.is_absolute() or days == PurePosixPath(directory) or PurePosixPath(directory) in days.parents \
            or days in PurePosixPath(directory).parents:
        raise VideoError(f"--days-dir {args.days_dir} must be an absolute path beside --dir {directory}, not in it")
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
    # after downloads, refetches (a mv breaks the old hardlink) and prunes: the day folders
    day_problems = sync_days(sh, plan, directory, args.days_dir, args.apply, args.prune, out)
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
    if args.apply and day_problems:
        out(f"ERROR {day_problems} day-folder problem(s) (see SKIP/WARN above); fix and run again")
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


# ---------------------------------------------------------------- day libraries
def options_drift(opts: dict) -> list[str]:
    """The DAY_OPTIONS keys a library's LibraryOptions differ in (fetchers: any non-empty list)."""
    bad = [k for k, v in DAY_OPTIONS.items() if k != "TypeOptions" and opts.get(k, v if k == "MetadataSavers" else None) != v]
    types = opts.get("TypeOptions") or []
    if not any(t.get("Type") == "Video" for t in types) or any(
            t.get(f) for t in types for f in ("MetadataFetchers", "ImageFetchers")):
        bad.append("TypeOptions (metadata/image fetchers)")
    return bad


def day_items(jf: Jellyfin, lib_id: str) -> list[tuple[str, str]]:
    """(path, name) of the library's videos in the server's name sort (what the clients show)."""
    res = jf.get("/Items", ParentId=lib_id, Recursive="true", IncludeItemTypes="Video", Fields="Path,SortName",
                 SortBy="SortName", SortOrder="Ascending", EnableImages="false", EnableUserData="false",
                 Limit="5000")
    return [(it.get("Path") or "", it.get("Name") or "") for it in (res or {}).get("Items", [])]


def hand_steps(libs: list[dict], out) -> None:
    out("HAND STEPS: the server did not let the API key create these libraries. In Jellyfin as an admin, "
        "Dashboard -> Libraries -> Libraries -> Add Media Library, once per library:")
    for lib in libs:
        out(f"  Display name {lib['name']!r} | Content type Home videos and photos | Folder "
            f"{DAYS_IN_JELLYFIN}/{lib['name']}")
    out("  each: every metadata downloader and image fetcher unticked; 'Prefer embedded titles over filenames' "
        "OFF; real-time monitoring, trickplay and chapter image extraction off. Then run jellyfin --apply "
        "again: it finds each library by exact name, type and folder, records it (origin hand) and verifies it")


def sync_day_libraries(jf: Jellyfin, plan: dict, users: list[dict], people: dict, old: list[dict],
                       prune: bool, wait: int, protected: set, out, sleep=time.sleep) -> tuple[list, list]:
    """One Home videos library per day folder; returns (managed records, problems).

    A plan library is matched by exact name; a match must be Home videos with exactly the day's
    folder, else it is a problem and left alone. Not there: POST /Library/VirtualFolders (refused
    -> hand steps). Drifted options are re-applied (POST /Library/VirtualFolders/LibraryOptions).
    Then each is scanned and verified: items in name order == the day's link paths in plan order.
    --prune deletes a recorded library whose name left the plan only when the server's library of
    that exact name has the recorded id, origin is api and the name is not protected."""
    plan_libs = day_libraries(plan)
    vfs = jf.get("/Library/VirtualFolders") or []
    by_name: dict[str, list] = {}
    for v in vfs:
        by_name.setdefault(v.get("Name"), []).append(v)
    recorded = {r["name"]: r for r in old}
    managed, problems, refused, todo = [], [], [], []
    for lib in plan_libs:
        name, path = lib["name"], f"{DAYS_IN_JELLYFIN}/{lib['name']}"
        found, rec = by_name.get(name, []), recorded.get(name)
        entry = {"login": lib["login"], "days": lib["days"], "name": name}
        if len(found) > 1:
            problems.append(f"Jellyfin has {len(found)} libraries named {name!r}; left alone, delete the extra by hand")
            continue
        if found:
            v = found[0]
            vid = norm(v.get("ItemId"))
            if (v.get("CollectionType") or "").lower() != COLLECTION_TYPE or list(v.get("Locations") or []) != [path]:
                problems.append(f"library {name!r} exists as {v.get('CollectionType')!r} on {v.get('Locations')}, not "
                                f"Home videos on [{path!r}]; left alone. Fix or delete it by hand, then run again")
                continue
            origin = "hand"
            if rec and rec.get("id") == vid:
                origin = rec.get("origin", "hand")
            elif rec:
                out(f"NOTE library {name!r} has id {vid}, recorded {rec.get('id')}: re-made by hand? recorded as hand")
            entry.update(id=vid, origin=origin)
            drift = options_drift(v.get("LibraryOptions") or {})
            if drift:
                merged = {**(v.get("LibraryOptions") or {}), **DAY_OPTIONS}
                try:
                    jf.write("POST", "/Library/VirtualFolders/LibraryOptions",
                             f"set day-library options of {name!r} ({', '.join(drift)})", {"Id": vid, "LibraryOptions": merged})
                except VideoError as e:
                    jf.actions[-1] += " -- FAILED"
                    problems.append(f"library {name!r}: could not set its options ({e}); in the dashboard set "
                                    f"{', '.join(drift)} as in the hand steps")
        else:
            if rec:
                out(f"NOTE recorded library {name!r} ({rec.get('id')}) is gone (deleted by hand?); creating it again")
            try:
                jf.write("POST", "/Library/VirtualFolders", f"create library {name!r} (Home videos, {path})",
                         {"LibraryOptions": DAY_OPTIONS},
                         params={"name": name, "collectionType": COLLECTION_TYPE, "paths": [path],
                                 "refreshLibrary": "false"})
            except VideoError as e:
                jf.actions[-1] += " -- FAILED"
                refused.append(lib)
                problems.append(f"could not create library {name!r}: {e}")
                continue
            if not jf.apply:
                managed.append({**entry, "id": f"dry-{name}", "origin": "api"})
                continue
            made = [v for v in jf.get("/Library/VirtualFolders") or [] if v.get("Name") == name]
            if len(made) != 1 or not ID_RE.match(norm(made[0].get("ItemId"))):
                problems.append(f"created library {name!r}, but it is not listed once with an id; check the dashboard")
                continue
            entry.update(id=norm(made[0]["ItemId"]), origin="api")
            drift = options_drift(made[0].get("LibraryOptions") or {})
            if drift:
                problems.append(f"library {name!r} was created without the options {', '.join(drift)}; set them in "
                                "the dashboard as in the hand steps")
        managed.append(entry)
        todo.append((entry, [f"{path}/{x['name']}" for x in lib["links"]]))
        sees = [f"{u['Name']} ({'all libraries' if (u.get('Policy') or {}).get('EnableAllFolders') else 'ticked'})"
                for u in users if (u.get("Policy") or {}).get("EnableAllFolders")
                or entry["id"] in {norm(x) for x in (u.get("Policy") or {}).get("EnabledFolders") or []}]
        owner = people[lib["login"]]["Name"]
        if not any(s.startswith(owner + " (") for s in sees) and jf.apply:
            out(f"WARN {owner} cannot see library {name!r} (not 'all libraries'); tick it under the user's Library "
                "access (owner decision: access is otherwise left as it is)")
        out(f"NOTE library {name!r} is visible to: {', '.join(sees) or 'no account'}")
    if refused:
        hand_steps(refused, out)
    # scan every library, then wait until each holds exactly its day's files
    for entry, _ in todo:
        jf.write("POST", f"/Items/{entry['id']}/Refresh", f"scan library {entry['name']!r}",
                 params={"Recursive": "true", "MetadataRefreshMode": "Default", "ImageRefreshMode": "Default",
                         "ReplaceAllMetadata": "false", "ReplaceAllImages": "false"})
    pending = [(e, w) for e, w in todo if not e["id"].startswith("dry-")]
    got: dict[str, list] = {}
    for n in range(max(1, wait // POLL) + 1):
        for e, w in pending:
            got[e["id"]] = day_items(jf, e["id"])
        if not jf.apply or all(sorted(p for p, _ in got[e["id"]]) == sorted(w) for e, w in pending):
            break
        if n < max(1, wait // POLL):
            sleep(POLL)
    for e, w in pending:
        items = got.get(e["id"], [])
        paths = [p for p, _ in items]
        if paths == w:
            stems = [PurePosixPath(p).stem for p in w]
            odd = [f"{nm!r} (file {s!r})" for (_, nm), s in zip(items, stems) if nm != s]
            if odd:
                out(f"WARN library {e['name']!r}: the order is right, but item name(s) differ from the file name: "
                    + ", ".join(odd[:3]) + (" ..." if len(odd) > 3 else ""))
            out(f"ok library {e['name']!r}: {len(w)} item(s) in workout order")
            continue
        msg = (f"library {e['name']!r}: {len(paths)} item(s) in name order, not the day's {len(w)} file(s) in "
               "workout order")
        if not jf.apply:
            out(f"NOTE {msg} (yet; the --apply scan updates it)")
        else:
            if sorted(paths) == sorted(w):
                msg += " (the right files, wrong order: is 'Prefer embedded titles' on?)"
            problems.append(msg)
    # records whose library left the plan
    keep = {e["name"] for e in managed}
    for r in old:
        if r["name"] in keep:
            continue
        if r["name"] in {lib["name"] for lib in plan_libs}:
            managed.append(r)                 # still planned but had a problem this run: keep the record
            continue
        found = by_name.get(r["name"], [])
        if not found:
            out(f"NOTE recorded library {r['name']!r} ({r.get('id')}) is gone; dropped from the record")
            continue
        if len(found) != 1 or norm(found[0].get("ItemId")) != r.get("id"):
            out(f"NOTE library {r['name']!r} no longer has the recorded id {r.get('id')}; not ours, never deleted, dropped")
            continue
        safe = (r.get("origin") == "api" and r["name"].lower() not in protected | PROTECTED
                and DAY_LIB_RE.match(r["name"]))
        if prune and safe:
            try:
                jf.write("DELETE", "/Library/VirtualFolders", f"delete library {r['name']!r} ({r['id']}): no longer in the plan",
                         params={"name": r["name"], "refreshLibrary": "false"})
            except VideoError as e:
                jf.actions[-1] += " -- FAILED"
                problems.append(f"could not delete library {r['name']!r}: {e}")
                managed.append(r)
            continue
        if prune:
            why = "was made by hand" if r.get("origin") != "api" else "is not a day-library name the tool may delete"
            out(f"EXTRA library {r['name']!r} {why}; the tool never deletes it: delete it in the dashboard if it "
                "should go, the next run drops the record")
        else:
            out(f"EXTRA library {r['name']!r} is no longer in the plan; --prune deletes it")
        managed.append(r)
    return managed, problems


def load_videos(path: Path = VIDEOS) -> dict:
    if not path.exists():
        return {"items": {}, "playlists": [], "libraries": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_videos(path: Path, items: dict, playlists: list, base: str, libraries: list | None = None) -> None:
    doc = {"_comment": HEADER, "base_url": base, "items": dict(sorted(items.items())),
           "playlists": sorted(playlists, key=lambda p: (p["login"], DAY_TAGS.index(p["days"][0]))),
           "libraries": sorted(libraries or [], key=lambda p: p["name"])}
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
        out("Planned day libraries (Home videos, one per day folder):")
        for lib in day_libraries(plan):
            out(f"  {lib['name']} -> {DAYS_IN_JELLYFIN}/{lib['name']}: " + ", ".join(x["name"] for x in lib["links"]))
        out(f"{len(want)} file(s) expected in the library, {len(pls)} playlist(s), {len(pls)} day librar(ies)")
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
            write_videos(videos_path, mapping, managed, LINK_BASE, old.get("libraries") or [])
            out(f"wrote {videos_path.name} before stopping: {len(mapping)} item id(s), {len(managed)} verified "
                "playlist(s); fix the error below and run again")
        raise
    libraries, lib_problems = sync_day_libraries(jf, plan, users, people, old.get("libraries") or [], args.prune,
                                                 args.wait, {args.library.lower()}, out, sleep)
    changed = (mapping != old.get("items") or managed != old.get("playlists") or old.get("base_url") != LINK_BASE
               or libraries != (old.get("libraries") or []))
    for a in actions:
        out(f"{'did' if args.apply else 'would'}: {a}")
    if args.apply:
        if changed:
            write_videos(videos_path, mapping, managed, LINK_BASE, libraries)
            out(f"wrote {videos_path.name}: {len(mapping)} item id(s), {len(managed)} playlist(s), "
                f"{len(libraries)} day librar(ies); commit it, then gen_wger.py --apply")
        else:
            out(f"{videos_path.name} unchanged")
    else:
        out(f"{len(actions)} write(s) planned (dry run; add --apply)"
            + (f"; {videos_path.name} would change" if changed else ""))
    rc = 0
    if problems:
        out(f"ERROR {len(problems)} old playlist(s) not removed (the new ones are made and recorded):")
        for p in problems:
            out(f"  {p}")
        rc = 1
    if lib_problems:
        out(f"ERROR {len(lib_problems)} day-library problem(s):")
        for p in lib_problems:
            out(f"  {p}")
        rc = 1
    return rc


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None, plan_path: Path = PLAN, shell: Shell | None = None,
         sleep=time.sleep, videos_path: Path | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="yt-dlp the plan videos onto the media share")
    d.add_argument("--dir", default=MEDIA_DIR, help=f"library directory on the media host (default {MEDIA_DIR})")
    d.add_argument("--days-dir", default=DAYS_DIR,
                   help=f"day folders of hardlinks on the media host (default {DAYS_DIR})")
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
