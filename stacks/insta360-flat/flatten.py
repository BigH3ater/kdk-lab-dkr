#!/usr/bin/env python3
"""insta360-flat: raw Insta360 X4 trips (/upload, read-only) -> finished flat stills (/flat).

One-shot. For every trip folder directly under UPLOAD that holds at least one .insp/.jpg:
  out dir  = /flat/<YYYY-MM of the earliest capture>-<trip name lowercased, non-alphanumerics -> '-'>
  bursts   = 3 or more captures sharing IMG_<date>_<time> keep only the lowest sequence
             number; a pair in the same second is two shots and both are kept
  fresh    = a raw whose ctime is under 15 min old (copy may still be running; SMB keeps
             mtime) is deferred to the next run: logged, counted, not a failure
  stills   = front `a` (heading 0, elevation -5) and back `b` (heading 180, elevation -15),
             leveled from the .insp accelerometer record, stereographic 95 deg, 3200x2400
             (1600x1200 below 8000 px source width), lossless PNG, PhotoCraft touch-up, JPEG q92.
             An .insp without the Insta360 trailer or its 0x300 motion record (truncated)
             FAILS instead of rendering unleveled; camera-stitched .jpg render unleveled.
  names    = YYYY-MM-DD_HHMM_<place>_<seq><a|b>.jpg; <place> from the trip's optional
             places.tsv (date YYYYMMDD, start HHMM, end HHMM, slug; slugified), else the trip slug
A still that already exists non-empty is skipped. Outputs are written to a dot-name in
the destination folder and renamed into place; nothing in /upload is written and nothing
in /flat is deleted. Ports ~/cad/tools/imaging (process-one.sh, level.py, insp_trailer.py,
flat-360.sh, touch.sh, slug.py) without changing the image logic.

Ends with one summary line; any failed capture -> one Pushover message and exit 1.
Stdlib only; ffmpeg/ffprobe/photocraft-cli come from the image (pinned in the Dockerfile).
"""
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UPLOAD = os.environ.get("UPLOAD_DIR", "/upload")
FLAT = os.environ.get("FLAT_DIR", "/flat")
# Optional override for testing: <PLACES_DIR>/<trip folder>.tsv wins over /upload/<trip>/places.tsv.
PLACES_DIR = os.environ.get("PLACES_DIR", "")
WORKERS = int(os.environ.get("WORKERS", "3"))
FISHEYE_FOV = os.environ.get("FISHEYE_FOV", "200")
PUSHOVER_FILE = os.environ.get("PUSHOVER_FILE", "/pushover.env")
SETTLE_S = int(os.environ.get("SETTLE_S", "900"))   # defer raws whose ctime is younger
BURST_MIN = 3   # captures in one second that count as a burst
TITLE = "insta360-flat failed"

NAME = re.compile(r"^IMG_(\d{8})_(\d{6})_(\d{2})_(\d{3})\.(insp|jpg)$", re.I)
VIEWS = (("a", 0.0, -5.0), ("b", 180.0, -15.0))   # suffix, heading, elevation
HFOV = 95
MAGIC = b"8db42d694ccc418790edff439fe026bf"


def log(msg):
    print(msg, flush=True)


def pushover(message):
    creds = {}
    try:
        for line in open(PUSHOVER_FILE, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip().strip('"')
    except OSError as e:
        log(f"pushover.env unreadable ({e.strerror}); not notified")
        return
    tok, usr = creds.get("PUSHOVER_TOKEN"), creds.get("PUSHOVER_USER")
    if not tok or not usr:
        log("pushover.env has no PUSHOVER_TOKEN/PUSHOVER_USER; not notified")
        return
    data = urllib.parse.urlencode({"token": tok, "user": usr, "title": TITLE, "message": message[:1000]}).encode()
    try:
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data, timeout=25).read()
    except Exception as e:  # noqa: BLE001
        log(f"pushover failed: {e}")


# ---- level.py / insp_trailer.py (unchanged maths) ----
def records(path):
    b = open(path, "rb").read()
    if b[-32:] != MAGIC:
        return None
    pos = len(b) - 72
    out = []
    while pos > 6:
        rid, ln = struct.unpack("<HI", b[pos - 6:pos])
        if ln == 0 or ln > pos:
            break
        out.append((rid, ln, b[pos - 6 - ln:pos - 6]))
        pos -= 6 + ln
    return out


def gravity(path):
    for rid, ln, d in records(path) or []:
        if rid == 0x300 and ln % 20 == 0 and ln:
            n = min(ln // 20, 30)   # only the first samples match the shutter pose
            cols = [[], [], []]
            for i in range(n):
                a = struct.unpack("<3H", d[i * 20 + 8:i * 20 + 14])
                for k in range(3):
                    cols[k].append((a[k] - 32768) / 1024.0)
            return [sorted(c)[n // 2] for c in cols]
    return None


def norm(v):
    l = math.sqrt(sum(x * x for x in v))
    return [x / l for x in v]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def angles(acc, heading, elev, lat_sign=1):
    # ffmpeg v360 frame: x right, y down, z forward (front lens). World up in that frame:
    U = norm([lat_sign * acc[2], acc[0], acc[1]])
    f = [0.0, 0.0, 1.0]
    H0 = [f[i] - dot(f, U) * U[i] for i in range(3)]
    if math.sqrt(dot(H0, H0)) < 0.35:   # lens nearly straight up/down: use the camera's top instead
        t = [0.0, -1.0, 0.0]
        H0 = [t[i] - dot(t, U) * U[i] for i in range(3)]
    H0 = norm(H0)
    R = cross(H0, U)
    h, e = math.radians(heading), math.radians(elev)
    H = [math.cos(h) * H0[i] + math.sin(h) * R[i] for i in range(3)]
    d = norm([math.cos(e) * H[i] + math.sin(e) * U[i] for i in range(3)])
    Ud = norm([U[i] - dot(U, d) * d[i] for i in range(3)])
    Rd = cross(d, Ud)
    M = [[Rd[i], -Ud[i], d[i]] for i in range(3)]   # columns: right, down, forward
    pitch = -math.asin(max(-1, min(1, M[1][2])))
    yaw = math.atan2(M[0][2], M[2][2])
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    Ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    Rx = [[1, 0, 0], [0, cp, -sp], [0, sp, cp]]
    A = [[sum(Ry[i][k] * Rx[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    Rz = [[sum(A[k][i] * M[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    roll = math.atan2(Rz[1][0], Rz[0][0])
    return [round(math.degrees(x), 2) for x in (yaw, pitch, roll)]


def level(acc, heading, elev):
    """The yaw/pitch/roll strings level.py printed (str() of the same floats)."""
    if acc is None:   # no trailer (.jpg) or no motion record: unleveled, as level.py did
        return str(heading), str(elev), "0"
    return tuple(str(x) for x in angles(acc, heading, elev))


# ---- trips, captures, names ----
def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "trip"


def load_places(trip):
    path = os.path.join(PLACES_DIR, f"{trip}.tsv") if PLACES_DIR else ""
    if not (path and os.path.isfile(path)):
        path = os.path.join(UPLOAD, trip, "places.tsv")
    rows = []
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            parts = line.strip().split("\t")
            if len(parts) == 4 and not parts[0].startswith("#"):
                rows.append(tuple(p.strip() for p in parts))
        log(f"{trip}: {len(rows)} place rows from {path}")
    return rows


def place(rows, d, t, fallback):
    # slugify: a places.tsv slug can never add '/' or '..' to the output path
    return slugify(next((s for dd, a, b, s in rows if dd == d and a <= t <= b), fallback))


def captures(trip_dir):
    """[(date, time, seq, file)]; a burst (BURST_MIN+ in one second) keeps its lowest seq."""
    groups, odd = {}, []
    for f in sorted(os.listdir(trip_dir)):
        if not f.lower().endswith((".insp", ".jpg")) or not os.path.isfile(os.path.join(trip_dir, f)):
            continue
        m = NAME.match(f)
        if not m:
            odd.append(f)
            continue
        seqs = groups.setdefault((m.group(1), m.group(2)), {})
        cand = (m.group(5).lower() != "insp", f)   # same seq twice: .insp before .jpg
        seq = m.group(4)
        if seq not in seqs or cand < seqs[seq]:
            seqs[seq] = cand
    out = []
    for (d, tm), seqs in sorted(groups.items()):
        keep = [min(seqs)] if len(seqs) >= BURST_MIN else sorted(seqs)
        out += [(d, tm, s, seqs[s][1]) for s in keep]
    return out, odd


def run(cmd):
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if p.returncode:
        tail = (p.stderr or p.stdout).strip().splitlines()[-3:]
        raise RuntimeError(f"{os.path.basename(cmd[0])} exit {p.returncode}: {' | '.join(tail)}")
    return p.stdout


def render(src, out_dir, base, missing):
    """Both views of one capture; only the `missing` suffixes are written."""
    width = int(run(["ffprobe", "-v", "error", "-f", "image2", "-show_entries", "stream=width",
                     "-of", "csv=p=0", src]).split()[0])
    w, h = (3200, 2400) if width >= 8000 else (1600, 1200)
    vfov = round(HFOV * (h / w), 2)   # flat-360.sh, sg projection
    proj = f"dfisheye:sg:ih_fov={FISHEYE_FOV}:iv_fov={FISHEYE_FOV}" if src.lower().endswith(".insp") else "e:sg"
    acc = gravity(src)   # like level.py: any file with the Insta360 trailer, .jpg included
    if acc is None and src.lower().endswith(".insp"):
        # A truncated copy loses the trailer; an unleveled still would then be kept forever.
        raise RuntimeError("no Insta360 trailer / 0x300 motion record (truncated .insp?); not rendered")
    with tempfile.TemporaryDirectory(prefix="i360.", dir="/tmp") as tmp:
        for sfx, heading, elev in VIEWS:
            if sfx not in missing:
                continue
            yaw, pitch, roll = level(acc, heading, elev)
            png, jpg = os.path.join(tmp, f"{sfx}.png"), os.path.join(tmp, f"{sfx}.jpg")
            run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "image2", "-i", src,
                 "-vf", f"v360={proj}:yaw={yaw}:pitch={pitch}:roll={roll}:h_fov={HFOV}:v_fov={vfov}"
                        f":w={w}:h={h}:interp=lanczos", "-frames:v", "1", "-q:v", "2", png])
            run(["photocraft-cli", "run", png, "--no-gpu",
                 "--cmd", "image.adjustments.shadowsHighlights", "--params", '{"shadowAmount":25,"highlightAmount":15}',
                 "--cmd", "image.autoContrast",
                 "--cmd", "image.adjustments.vibrance", "--params", '{"vibrance":18,"saturation":4}',
                 "--cmd", "filter.sharpen.smartSharpen", "--params", '{"amount":60,"radius":1,"reduceNoise":10}',
                 "--out", jpg, "--quality", "92"])
            if os.path.getsize(jpg) == 0:
                raise RuntimeError("photocraft-cli wrote an empty file")
            final = os.path.join(out_dir, f"{base}{sfx}.jpg")
            part = os.path.join(out_dir, f".{base}{sfx}.jpg.part")
            shutil.copyfile(jpg, part)
            os.replace(part, final)


def done(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def main():
    t0 = time.time()
    stats = {"trips": 0, "captures": 0, "made": 0, "skipped": 0, "deferred": 0, "failed": 0}
    failures = []
    jobs = []
    for trip in sorted(os.listdir(UPLOAD)):
        trip_dir = os.path.join(UPLOAD, trip)
        if trip.startswith(".") or not os.path.isdir(trip_dir):
            continue
        caps, odd = captures(trip_dir)
        for f in odd:
            log(f"{trip}: ignored {f} (name is not IMG_<date>_<time>_<nn>_<seq>)")
        if not caps:
            continue
        slug = slugify(trip)
        first = min(d for d, _t, _s, _f in caps)
        out_dir = os.path.join(FLAT, f"{first[:4]}-{first[4:6]}-{slug}")
        rows = load_places(trip)
        stats["trips"] += 1
        stats["captures"] += len(caps)
        log(f"{trip}: {len(caps)} captures -> {out_dir}")
        for d, tm, seq, f in caps:
            base = f"{d[:4]}-{d[4:6]}-{d[6:8]}_{tm[:4]}_{place(rows, d, tm[:4], slug)}_{seq}"
            jobs.append((os.path.join(trip_dir, f), out_dir, base))

    def one(job):
        src, out_dir, base = job
        missing = [s for s, _h, _e in VIEWS if not done(os.path.join(out_dir, f"{base}{s}.jpg"))]
        if not missing:
            return "skipped", None
        age = time.time() - os.stat(src).st_ctime   # ctime: SMB copies keep the camera mtime
        if age < SETTLE_S:
            log(f"deferred {os.path.basename(src)}: ctime {age:.0f}s ago (< {SETTLE_S}s, copy may still run)")
            return "deferred", None
        t = time.time()
        try:
            os.makedirs(out_dir, exist_ok=True)
            render(src, out_dir, base, missing)
        except Exception as e:  # noqa: BLE001
            log(f"FAILED {os.path.basename(src)}: {e}")
            return "failed", f"{os.path.basename(src)}: {e}"
        log(f"ok {os.path.basename(src)} -> {base}[{''.join(missing)}].jpg {time.time() - t:.1f}s")
        return "made", None

    with ThreadPoolExecutor(max_workers=max(1, WORKERS)) as ex:
        for result, err in ex.map(one, jobs):
            stats[result] += 1
            if err:
                failures.append(err)

    log("insta360-flat: trips={trips} captures={captures} made={made} skipped={skipped} deferred={deferred} failed={failed}".format(**stats)
        + f" elapsed={time.time() - t0:.0f}s")
    if failures:
        pushover(f"{len(failures)} of {stats['captures']} captures failed; first: " + "; ".join(failures[:3]))
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"FATAL {type(e).__name__}: {e}")
        pushover(f"{type(e).__name__}: {e}")
        sys.exit(1)
