"""exercise_videos.py against a fake shell (download) and an in-memory fake Jellyfin 12 API (jellyfin).

The fake Jellyfin keeps users with library policies, one "Exercise" Home videos library, a
"disk" of files that a library refresh turns into items (id = md5 of the path, as stable as
Jellyfin's), and playlists with owner, OpenAccess, shares and ordered entries.

It answers like Jellyfin 12.4 does for an API key (a token with no user behind it, observed on the
real server 2026-11-14 and in PlaylistsController.cs at v12.4): every /Playlists/{id} call that uses
the calling user (GET /Playlists/{id}, /Users, POST /Playlists/{id}, DELETE .../Items, .../Move)
is a 400; the user-scoped reads take ?userId= and give a non-owner 404."""
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import exercise_videos as ev  # noqa: E402

PLAN = ev.load_plan()
VIDEO_KEYS = {k for k, ex in PLAN["exercises"].items() if ex["video"] is not None}   # treadmill-walk has none
LOC = "/data/media/exercise"
DAYS_LOC = "/data/media/exercise-days"
LIB = "a" * 32
JMACK, RACH, OTHER, ADMIN = "1" * 32, "2" * 32, "3" * 32, "4" * 32


def names(plan=PLAN):
    return sorted(ev.wanted_files(plan))


# the names the tool made before 2026-11-14 (live on the server, ids as in wger_videos.json then)
OLD_NAMES = ["Jacob · Mon Lower A", "Jacob · Wed Upper A", "Jacob · Fri Lower B", "Jacob · Sat Upper B",
             "Rachel · Mon Strength A", "Rachel · Tue/Fri Cardio", "Rachel · Wed/Sat/Sun Daily parts",
             "Rachel · Thu Strength B"]
OLD_IDS = ["149d6a5fe1ad9dded863c4ade2095567", "ccc02d5c5f0e9ffc19fdd6372acf695a", "1c808e496244d57c2a9480017e879bf6",
           "b2d8d6ccf223f70207bf06660fa63099", "0526dc8adc5708473cb946a196d78729", "1b06374812eb4dd989f893ba4c44dda2",
           "2a49a7c00316f3c9a4fc6119f96956ad", "6a1a538dad131ce0e7ed3deb3756384f"]


# ---------------------------------------------------------------- the plan side
def test_file_names_come_from_key_and_youtube_id_only():
    ex = PLAN["exercises"]["plank"]
    assert ev.file_name("plank", ex) == f"plank [{ex['video'][-11:]}].mp4"
    assert ev.FILE_RE.match(ev.file_name("plank", ex))
    renamed = {**ex, "name": "Something else entirely"}
    assert ev.file_name("plank", renamed) == ev.file_name("plank", ex)
    assert len(names()) == len(PLAN["exercises"]) - 1 == 46      # treadmill-walk: no video, no file
    assert not [n for n in names() if n.startswith("treadmill-walk")]


def test_playlists_one_per_person_and_day_in_workout_order():
    pls = {p["name"]: p for p in ev.plan_playlists(PLAN)}
    assert list(pls) == ["Jacob · 1 Mon Lower A", "Jacob · 2 Wed Upper A", "Jacob · 3 Fri Lower B", "Jacob · 4 Sat Upper B",
                         "Rachel · 1 Mon Strength A", "Rachel · 2 Tue/Fri Cardio", "Rachel · 3 Wed/Sat/Sun Daily parts",
                         "Rachel · 4 Thu Strength B"]
    mon = pls["Jacob · 1 Mon Lower A"]
    assert mon["login"] == "jmack" and mon["days"] == ["mon"]
    assert mon["keys"][:3] == ["goblet-squat", "db-rdl", "step-up"] and mon["keys"][-1] == "bird-dog"
    for p in pls.values():                                      # treadmill-walk has no video: in no playlist
        assert "treadmill-walk" not in p["keys"]
        if p["login"] == "jmack":
            assert len(p["keys"]) == 9, p["name"]
    assert pls["Rachel · 1 Mon Strength A"]["keys"] == ["double-calf-raise", "hip-flexor-stretch", "box-squat",
                                                       "pallof-press", "incline-push-up", "wall-squat", "cow-cat"]
    assert pls["Rachel · 3 Wed/Sat/Sun Daily parts"]["days"] == ["wed", "sat", "sun"]


def test_names_sort_in_week_order_per_person():
    pls = ev.plan_playlists(PLAN)
    for login in ("jmack", "rach"):
        mine = [p["name"] for p in pls if p["login"] == login]
        assert sorted(mine) == mine, login                              # plain name sort = week order
    assert [p["old_name"] for p in pls] == OLD_NAMES


def test_ten_or_more_playlists_are_zero_padded():
    ex = {f"move-{i}": {"name": f"Move {i}", "video": f"https://www.youtube.com/watch?v={i:011d}"} for i in range(11)}
    routines = [{"day": ev.DAY_TAGS[min(i, 6)], "name": f"R{i}", "slots": [{"exercise": f"move-{i}"}]}
                for i in range(11)]
    plan = {"exercises": ex, "people": {"x": {"name": "X", "routines": routines}}}
    got = [p["name"] for p in ev.plan_playlists(plan)]
    assert got[0] == "X · 01 Mon R0" and got[9].startswith("X · 10 ") and len(got) == 11
    assert sorted(got) == got


def test_bad_plan_key_or_video_is_rejected(tmp_path):
    plan = copy.deepcopy(PLAN)
    plan["exercises"]["Bad Key"] = plan["exercises"].pop("plank")
    plan["exercises"]["walking"]["video"] = "https://youtu.be/x"
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    with pytest.raises(ev.VideoError) as e:
        ev.load_plan(p)
    assert "Bad Key" in str(e.value) and "exercises.walking: video" in str(e.value)


def test_only_an_explicit_null_means_no_video(tmp_path):
    assert PLAN["exercises"]["treadmill-walk"]["video"] is None          # owner 2026-11-14
    plan = copy.deepcopy(PLAN)
    del plan["exercises"]["plank"]["video"]
    plan["exercises"]["goblet-squat"]["video"] = ""
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    with pytest.raises(ev.VideoError) as e:
        ev.load_plan(p)
    msg = str(e.value)
    assert "exercises.plank: video" in msg and "exercises.goblet-squat: video" in msg and "treadmill" not in msg


def test_a_day_of_no_video_exercises_gets_no_playlist():
    plan = copy.deepcopy(PLAN)
    person = plan["people"]["jmack"]
    person["routines"].append({"day": "sun", "name": "Sun · Walk", "slots": [{"exercise": "treadmill-walk"}]})
    names_ = [p["name"] for p in ev.plan_playlists(plan) if p["login"] == "jmack"]
    assert len(names_) == 4 and not [n for n in names_ if "Sun" in n]


def test_the_old_treadmill_file_is_extra_and_prune_removes_it(capsys):
    old = "treadmill-walk [jkJkmy8IHXU].mp4"
    sh = FakeShell(files=[*names(), old])
    assert dl(shell=sh) == 0
    assert f"EXTRA {old}: no longer in the plan" in capsys.readouterr().out
    assert dl("--apply", "--prune", shell=sh) == 0
    assert old not in sh.files and sh.files == set(names()) and sh.runs() == []


def test_image_is_pinned_by_digest():
    assert "@sha256:" in ev.image_ref()


# ---------------------------------------------------------------- download (fake shell)
class FakeShell:
    """Plays find/test/mkdir/rm/docker against a dict-based directory and its .incoming temp dir.

    fail: {plan key: number of failing attempts} (-1 = always) -> yt-dlp exits 1 with YouTube's 403
    and leaves a .part file in .incoming. silent: keys whose run exits 0 without a file."""

    TMP = f"/mnt/media/exercise/{ev.INCOMING}"
    DAYS = ev.DAYS_DIR

    def __init__(self, files=None, exists=True, fail=None, silent=(), incoming=None, link=False,
                 sizes=None, mv_fails=False, days=None, days_exists=True):
        self.files = set(files or [])
        self.ino = {}                                                   # file -> inode (lazy; a mv gets a new one)
        self.next_ino = 1000
        self.days = {k: dict(v) for k, v in (days or {}).items()}       # folder -> {file: (type, inode)}
        self.days_top = {k: "d" for k in self.days}                     # top-level name -> find %y type
        self.days_exists = days_exists
        self.exists = exists
        self.calls = []
        self.fail = dict(fail or {})
        self.silent = set(silent)
        self.incoming = None if incoming is None else set(incoming)     # None = no temp dir
        self.link = link
        self.sizes = dict(sizes or {})                                  # file -> "WxH" for --probe
        self.mv_fails = mv_fails
        self.replaced = []                                              # files a mv overwrote

    def inode(self, f):
        if f not in self.ino:
            self.next_ino += 1
            self.ino[f] = str(self.next_ino)
        return self.ino[f]

    def links(self):
        """{folder: [file names]} of the hardlinks (type f) in the day folders."""
        return {d: sorted(f for f, (t, _) in fs.items() if t == "f") for d, fs in self.days.items()}

    def days_call(self, argv):
        """find/test/mkdir/ln/rm/rmdir inside the day folders dir; None when argv is not one of those."""
        D = self.DAYS
        if argv[0] == "find" and argv[1] == D:
            if not self.days_exists:
                return 1, ""
            lines = [f"{t}\t77\t{d}" for d, t in self.days_top.items()]
            for d, fs in self.days.items():
                lines += [f"{t}\t{i}\t{d}/{f}" for f, (t, i) in fs.items()]
            return 0, "".join(x + "\n" for x in sorted(lines))
        if argv[0] == "test" and argv[-1] == D:
            return (0 if self.days_exists else 1), ""
        if argv[0] == "find" and argv[-1] == "%i\\t%f\\n":
            if not self.exists:
                return 1, ""
            return 0, "".join(f"{self.inode(f)}\t{f}\n" for f in sorted(self.files))
        paths = [a for a in argv[1:] if a.startswith(D)]
        if not paths:
            return None
        for a in paths:                                                 # never outside the day folders dir
            assert a == D or (a.startswith(D + "/") and ".." not in a.split("/")), argv
        rel = paths[-1][len(D) + 1:].split("/") if paths[-1] != D else []
        if argv[:3] == ["mkdir", "-p", "--"]:
            if not rel:
                self.days_exists = True
            else:
                assert len(rel) == 1, argv
                self.days_top[rel[0]] = "d"
                self.days.setdefault(rel[0], {})
        elif argv[:3] == ["ln", "-fT", "--"]:
            src = argv[3]
            assert str(Path(src).parent) == "/mnt/media/exercise" and Path(src).name in self.files, argv
            assert len(rel) == 2 and self.days_top.get(rel[0]) == "d", argv
            self.days[rel[0]][rel[1]] = ("f", self.inode(Path(src).name))
        elif argv[:3] == ["rm", "-f", "--"]:
            assert len(rel) == 2, argv
            self.days[rel[0]].pop(rel[1], None)
        elif argv[:2] == ["rmdir", "--"]:
            assert len(rel) == 1 and not self.days[rel[0]], argv
            del self.days[rel[0]], self.days_top[rel[0]]
        else:
            raise AssertionError(argv)
        return 0, ""

    def __call__(self, argv, check=True):
        self.calls.append(argv)
        rc, out, err = 0, "", ""
        d = self.days_call(argv)
        if d is not None:
            p = subprocess.CompletedProcess(argv, d[0], d[1], "")
            if check and d[0]:
                raise ev.VideoError("failed")
            return p
        tmp = len(argv) > 1 and argv[-1] == self.TMP
        in_tmp = len(argv) > 1 and argv[-1].startswith(self.TMP + "/")
        if argv[0] == "find" and argv[1] == self.TMP:
            rc = 0 if self.incoming is not None else 1
            out = "".join(f + "\n" for f in sorted(self.incoming or ()))
        elif argv[0] == "find":
            if not self.exists:
                rc = 1
            out = "".join(f + "\n" for f in sorted(self.files))
        elif argv[0] == "test" and tmp:
            rc = {"-L": 0 if self.link else 1,
                  "-e": 0 if self.link or self.incoming is not None else 1,
                  "-d": 0 if self.incoming is not None else 1}[argv[1]]
        elif argv[0] == "test" and in_tmp:
            here = Path(argv[-1]).name in (self.incoming or ())
            rc = 0 if here and argv[1] in ("-e", "-s") else 1
        elif argv[0] == "test":
            rc = 0 if self.exists else 1
        elif argv[0] == "mv":
            src, dst = argv[-2], argv[-1]
            assert argv[:3] == ["mv", "-f", "--"] and src.startswith(self.TMP + "/"), argv
            assert Path(src).name == Path(dst).name and str(Path(dst).parent) == "/mnt/media/exercise", argv
            if self.mv_fails:
                rc, err = 1, "mv: cannot move: Permission denied"
            else:
                self.incoming.discard(Path(src).name)
                if Path(dst).name in self.files:
                    self.replaced.append(Path(dst).name)
                self.files.add(Path(dst).name)
                self.ino.pop(Path(dst).name, None)                      # a new file: a new inode
        elif argv[0] == "mkdir":
            self.exists = True
        elif argv[0] == "rm" and argv[1] == "-rf":
            assert argv[-1] == self.TMP, argv                          # never anything but the temp dir
            self.incoming = None
        elif argv[0] == "rm":
            self.files.discard(Path(argv[-1]).name)
        elif "run" in argv and "--entrypoint" in argv:                   # download --probe
            out = "".join(f"{self.sizes.get(f, '1920x1080')}\t{f}\n" for f in sorted(self.files) if f.endswith(".mp4"))
        elif "run" in argv:
            out_tpl, url = argv[-2], argv[-1]
            home = argv[argv.index("-P") + 1]
            name = out_tpl.replace("%(id)s", url[-11:]).replace("%(ext)s", "mp4")
            key = name.split(" ")[0]
            self.incoming = self.incoming if self.incoming is not None else set()
            if self.fail.get(key, 0):
                self.fail[key] -= 1
                self.incoming.add(name.replace(".mp4", ".f137.mp4.part"))
                rc, err = 1, "[youtube] x: Downloading\nERROR: unable to download video data: HTTP Error 403: Forbidden\n"
            elif key not in self.silent:
                self.incoming = {x for x in self.incoming if not x.startswith(key + " ")}
                if home == f"/downloads/{ev.INCOMING}":                 # --refetch: lands in the temp dir
                    self.incoming.add(name)
                else:
                    assert home == "/downloads", argv
                    self.files.add(name)
        else:
            raise AssertionError(argv)
        p = subprocess.CompletedProcess(argv, rc, out, err)
        if check and rc:
            raise ev.VideoError("failed")
        return p

    def writes(self):
        return [c for c in self.calls if c[0] in ("mkdir", "rm", "mv", "ln", "rmdir")
                or ("run" in c and "--entrypoint" not in c)]

    def runs(self, key=None):
        return [c for c in self.calls if "run" in c and (key is None or c[-2].startswith(key + " "))]


def dl(*args, shell, sleeps=None):
    return ev.main(["download", *args], shell=shell,
                   sleep=(sleeps.append if sleeps is not None else lambda s: None))


def test_download_dry_run_writes_nothing(capsys):
    sh = FakeShell(exists=False)
    assert dl(shell=sh) == 0
    assert sh.writes() == []
    out = capsys.readouterr().out
    assert f"{len(names())} missing" in out and "dry run:" in out


def test_download_apply_fetches_only_missing_files_as_1000_with_the_pinned_image(capsys):
    have = names()[:5]
    sh = FakeShell(files=have)
    assert dl("--apply", shell=sh) == 0
    runs = [c for c in sh.calls if "run" in c]
    assert len(runs) == len(names()) - 5
    r = runs[0]
    assert r[:3] == ["sudo", "-n", "docker"] and r[r.index("--user") + 1] == "1000:1000"
    assert ev.image_ref() in r and "/mnt/media/exercise:/downloads" in r
    assert "--merge-output-format" in r and r[r.index("-f") + 1] == ev.FORMAT
    assert sh.files == set(names())
    sh.calls.clear()
    assert dl("--apply", shell=sh) == 0 and sh.writes() == []        # idempotent


def test_download_embeds_the_plan_name_as_title():
    argv = ev.ytdlp_argv("plank", PLAN["exercises"]["plank"])
    i = argv.index("--replace-in-metadata")
    assert argv[i + 1:i + 4] == ["title", "(?s).+", PLAN["exercises"]["plank"]["name"]]
    assert argv[argv.index("-o") + 1] == "plank [%(id)s].%(ext)s"


def test_download_reports_a_file_that_did_not_appear(capsys):
    sh = FakeShell(silent={"plank"})
    assert dl("--apply", shell=sh) == 1
    out = capsys.readouterr().out
    assert f"1 of {len(names())} video(s) not created" in out and "  plank [" in out
    assert "exited 0 but the file is not there" in out


def test_download_passes_yt_dlp_retry_options():
    argv = ev.ytdlp_argv("plank", PLAN["exercises"]["plank"])
    assert argv[argv.index("--extractor-retries") + 1] == "5"
    sleeps = [argv[i + 1] for i, a in enumerate(argv) if a == "--retry-sleep"]
    assert sleeps == ["http:exp=1:30", "fragment:exp=1:30", "extractor:5"]
    assert argv[-1] == PLAN["exercises"]["plank"]["video"]          # url stays last


def test_a_403_is_retried_and_the_retry_succeeds(capsys):
    sleeps = []
    sh = FakeShell(fail={"plank": 2})
    assert dl("--apply", shell=sh, sleeps=sleeps) == 0
    assert len(sh.runs("plank")) == 3 and sh.files == set(names())
    out = capsys.readouterr().out
    assert "retry plank [" in out and "(1/4 failed: ERROR: unable to download video data: HTTP Error 403" in out
    assert "FAILED" not in out and "not created" not in out
    assert [s for s in sleeps if s != ev.BETWEEN] == [ev.RETRY_PAUSE, 2 * ev.RETRY_PAUSE]
    assert sleeps.count(ev.BETWEEN) == len(names()) - 1                 # a pause between videos
    assert sh.incoming is None and "removed .incoming/" in out          # the .part leftover is gone


def test_a_permanent_failure_does_not_stop_the_run(capsys):
    order = names()
    first = ev.wanted_files(PLAN)[order[0]]
    sh = FakeShell(fail={first: -1})
    assert dl("--apply", shell=sh) == 1
    assert len(sh.runs(first)) == ev.ATTEMPTS
    assert sh.files == set(order[1:])                                  # every later video still came
    out = capsys.readouterr().out
    assert f"FAILED {order[0]} after {ev.ATTEMPTS} attempts" in out
    assert f"1 of {len(order)} video(s) not created" in out
    assert f"  {order[0]} <- {PLAN['exercises'][first]['video']}" in out
    # only the failed video's .part was there, no finished video: the temp dir is removed
    assert sh.incoming is None
    assert [c for c in sh.calls if c[0] == "rm"] == [["rm", "-rf", "--", FakeShell.TMP]]
    sh.calls.clear()
    sh.fail.clear()
    assert dl("--apply", shell=sh) == 0 and len(sh.runs()) == 1        # the re-run fetches only that one


def test_temp_dir_with_a_finished_video_is_kept(capsys):
    done = names()[0]
    sh = FakeShell(files=names(), incoming={done, "x.part"})
    assert dl("--apply", shell=sh) == 0
    assert sh.incoming == {done, "x.part"} and not [c for c in sh.calls if c[0] == "rm"]
    assert "holds finished video(s), left alone" in capsys.readouterr().out


def test_temp_dir_symlink_is_never_followed_or_removed(capsys):
    sh = FakeShell(files=names(), link=True)
    assert dl("--apply", shell=sh) == 0
    assert not [c for c in sh.calls if c[0] == "rm" or (c[0] == "find" and c[1] == FakeShell.TMP)]
    assert "not a plain directory" in capsys.readouterr().out


def test_temp_dir_cleanup_only_with_apply_and_not_reported_as_other(capsys):
    sh = FakeShell(files=[*names(), ev.INCOMING], incoming={"a [AAAAAAAAAAA].f137.mp4.part"})
    assert dl(shell=sh) == 0
    out = capsys.readouterr().out
    assert sh.writes() == [] and sh.incoming
    assert "0 other file(s)" in out and "OTHER" not in out and "--apply removes it" in out
    assert dl("--apply", shell=sh) == 0
    assert sh.incoming is None and [c for c in sh.calls if c[0] == "rm"] == [["rm", "-rf", "--", FakeShell.TMP]]


def test_prune_only_deletes_plan_pattern_files_and_only_with_apply(capsys):
    old = "old-move [AAAAAAAAAAA].mp4"
    foreign = ["notes.txt", "Some Movie (2020).mp4", ".hidden"]
    sh = FakeShell(files=[*names(), old, *foreign])
    assert dl("--prune", shell=sh) == 0                      # dry run
    assert sh.writes() == [] and old in sh.files
    assert "EXTRA old-move" in capsys.readouterr().out
    assert dl("--apply", shell=sh) == 0                      # no --prune: kept
    assert old in sh.files
    assert dl("--apply", "--prune", shell=sh) == 0
    assert old not in sh.files and set(foreign) <= sh.files
    rms = [c for c in sh.calls if c[0] == "rm"]
    assert rms == [["rm", "-f", "--", f"/mnt/media/exercise/{old}"]]


def test_format_caps_the_short_side_not_the_height():
    # [height<=1080] dropped portrait 720x1280 / 1080x1920 streams (goblet-squat, suitcase-carry
    # came down at 480x854); the cap is -S res:1080 (res = the smaller dimension)
    argv = ev.ytdlp_argv("plank", PLAN["exercises"]["plank"])
    f = argv[argv.index("-f") + 1]
    assert "height" not in f and "width" not in f
    assert f.split("/") == ["bv*[vcodec^=avc1]+ba[acodec^=mp4a]", "b[vcodec^=avc1][acodec^=mp4a]"]
    assert argv[argv.index("-S") + 1] == "res:1080"
    assert argv[argv.index("--merge-output-format") + 1] == "mp4"
    assert argv[argv.index("-P") + 1] == "/downloads"


GOBLET = ev.file_name("goblet-squat", PLAN["exercises"]["goblet-squat"])
SUITCASE = ev.file_name("suitcase-carry", PLAN["exercises"]["suitcase-carry"])
TMPF = f"{FakeShell.TMP}/{GOBLET}"


def test_refetch_dry_run_names_the_file_and_writes_nothing(capsys):
    sh = FakeShell(files=names())
    assert dl("--refetch", "goblet-squat", "--refetch", "suitcase-carry", shell=sh) == 0
    out = capsys.readouterr().out
    assert sh.writes() == []
    assert f"would replace {GOBLET} <- {PLAN['exercises']['goblet-squat']['video']}" in out
    assert f"would replace {SUITCASE}" in out and "0 download(s), 2 replace(s)" in out


def test_refetch_downloads_into_the_temp_dir_then_moves_over_the_same_path(capsys):
    sh = FakeShell(files=names())
    assert dl("--apply", "--refetch", "goblet-squat", shell=sh) == 0
    run, = sh.runs()
    assert run[run.index("-P") + 1] == f"/downloads/{ev.INCOMING}"     # never written over in place
    assert run[run.index("-S") + 1] == "res:1080"
    i = sh.calls.index(run)
    assert ["test", "-e", TMPF] in sh.calls[:i]                          # nothing stale in the way
    assert sh.calls[i + 1:i + 3] == [["test", "-s", TMPF], ["mv", "-f", "--", TMPF, f"/mnt/media/exercise/{GOBLET}"]]
    assert sh.replaced == [GOBLET] and sh.files == set(names())          # same name: same Jellyfin item
    assert sh.incoming is None                                           # temp dir cleaned afterwards
    out = capsys.readouterr().out
    assert f"replaced {GOBLET}" in out and "next: exercise_videos.py jellyfin --apply" in out


def test_a_failed_refetch_keeps_the_old_file(capsys):
    sh = FakeShell(files=names(), fail={"goblet-squat": -1})
    assert dl("--apply", "--refetch", "goblet-squat", shell=sh) == 1
    assert len(sh.runs()) == ev.ATTEMPTS and not [c for c in sh.calls if c[0] == "mv"]
    assert GOBLET in sh.files and not sh.replaced
    out = capsys.readouterr().out
    assert "1 of 1 refetch(es) failed; the old file is still in place" in out and "  --refetch goblet-squat" in out


def test_a_refetch_whose_move_fails_keeps_the_new_file_in_the_temp_dir(capsys):
    sh = FakeShell(files=names(), mv_fails=True)
    assert dl("--apply", "--refetch", "goblet-squat", shell=sh) == 1
    assert sh.incoming == {GOBLET} and not sh.replaced                   # finished video: never auto-deleted
    out = capsys.readouterr().out
    assert "could not move the new file into place" in out and "holds finished video(s), left alone" in out


def test_a_leftover_in_the_temp_dir_blocks_the_refetch(capsys):
    sh = FakeShell(files=names(), incoming={GOBLET})
    assert dl("--apply", "--refetch", "goblet-squat", shell=sh) == 1
    assert sh.runs() == [] and not [c for c in sh.calls if c[0] == "mv"]
    assert f"{ev.INCOMING}/{GOBLET} already exists" in capsys.readouterr().out


def test_refetch_of_a_missing_file_is_a_plain_download_and_unknown_keys_fail(capsys):
    sh = FakeShell(files=[n for n in names() if n != GOBLET])
    assert dl("--apply", "--refetch", "goblet-squat", shell=sh) == 0
    run, = sh.runs()
    assert run[run.index("-P") + 1] == "/downloads" and not sh.replaced
    assert "is not on the share yet; it is a normal download" in capsys.readouterr().out
    assert dl("--refetch", "no-such-move", shell=FakeShell(files=names())) == 2
    assert "--refetch no-such-move: not a plan exercise key with a video" in capsys.readouterr().err
    assert dl("--refetch", "treadmill-walk", shell=FakeShell(files=names())) == 2   # no video by design


def test_probe_flags_files_below_1080_on_the_short_side(capsys):
    sizes = {GOBLET: "480x854", SUITCASE: "1080x1920", "plank [3QZlgJ40LfU].mp4": "1280x720"}
    sh = FakeShell(files=names(), sizes=sizes)
    assert dl("--probe", shell=sh) == 0
    run, = [c for c in sh.calls if "run" in c]
    assert run[run.index("--network") + 1] == "none" and "/mnt/media/exercise:/downloads:ro" in run
    assert run[run.index("--entrypoint") + 1] == "sh" and ev.image_ref() in run and sh.writes() == []
    out = capsys.readouterr().out
    assert f"LOW 480x854 {GOBLET} <- {PLAN['exercises']['goblet-squat']['video']}" in out
    assert "LOW 1280x720 plank [" in out and f"ok  1080x1920 {SUITCASE}" in out
    assert f"probe: {len(names())} plan video(s), 2 below 1080 on the short side" in out


def test_probe_reports_missing_files_and_only_reads(capsys):
    sh = FakeShell(files=names()[1:])
    assert dl("--probe", shell=sh) == 0
    assert f"MISSING {names()[0]}" in capsys.readouterr().out
    assert dl("--probe", "--apply", shell=sh) == 2


def test_probe_script_runs_in_a_real_shell(tmp_path):
    # the sh -c script itself, with a stub ffprobe: tab-separated "WxH<TAB>name" per mp4, odd names fine
    (tmp_path / "bin").mkdir()
    stub = tmp_path / "bin" / "ffprobe"
    stub.write_text("#!/bin/sh\nfor a; do f=$a; done\ncase $f in *goblet*) echo 480x854;; *) echo 1920x1080;; esac\n")
    stub.chmod(0o755)
    d = tmp_path / "downloads"
    d.mkdir()
    for n in (GOBLET, "plank [3QZlgJ40LfU].mp4", "notes.txt"):
        (d / n).write_text("x")
    script = ev.PROBE_SH.replace("cd /downloads", f"cd {d}")
    p = subprocess.run(["sh", "-c", script], capture_output=True, text=True,
                       env={"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin"})
    assert p.returncode == 0, p.stderr
    assert sorted(p.stdout.splitlines()) == [f"1920x1080\tplank [3QZlgJ40LfU].mp4", f"480x854\t{GOBLET}"]


def test_shell_wraps_commands_for_ssh():
    seen = []
    sh = ev.Shell("kdkadmin@host", run=lambda cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    sh(["rm", "-f", "--", "/mnt/media/exercise/a [b].mp4"])
    assert seen[0][:4] == ["ssh", "-o", "BatchMode=yes", "kdkadmin@host"]
    assert seen[0][4] == "rm -f -- '/mnt/media/exercise/a [b].mp4'"


# ---------------------------------------------------------------- Jellyfin (fake API)
class Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body
        self.text = json.dumps(body)
        self.content = b"" if body is None else self.text.encode()

    def json(self):
        return self._body


def iid(path):
    return hashlib.md5(path.encode()).hexdigest()


class FakeJellyfin:
    KEY = "jf-key"

    def __init__(self, files=(), lib_type="homevideos"):
        def user(uid, name, all_folders=False, folders=(), admin=False):
            return {"Id": uid, "Name": name, "Policy": {"EnableAllFolders": all_folders, "IsAdministrator": admin,
                                                        "EnabledFolders": list(folders)}}
        dashed = f"{LIB[:8]}-{LIB[8:12]}-{LIB[12:16]}-{LIB[16:20]}-{LIB[20:]}"
        self.users = [user(JMACK, "jmack", folders=[dashed]), user(RACH, "rach", folders=[LIB]),
                      user(OTHER, "guest", folders=["b" * 32]), user(ADMIN, "kdkadmin", folders=["b" * 32], admin=True)]
        self.lib = {"Name": "Exercise", "CollectionType": lib_type, "ItemId": LIB, "Locations": [LOC],
                    "LibraryOptions": {"EnableEmbeddedTitles": True}}
        self.disk = set(files)
        self.items = {}            # id -> path (after a scan)
        self.playlists = {}        # id -> {name, owner, public, shares, ids}
        self.writes = []
        self.n = 0
        self.share_on_create = None    # a user id the server adds as a share on create (a bad server)
        self.fail_create = set()       # playlist names whose create answers 500 once
        self.fail_delete = set()       # item ids whose DELETE /Items/{id} answers 500
        # day libraries: the day folders as the container sees them, the virtual folders, their items
        self.day_disk = {lib["name"]: [x["name"] for x in lib["links"]] for lib in ev.day_libraries(PLAN)}
        self.vlibs = {}                # name -> VirtualFolderInfo (beside Exercise and Movies)
        self.day_items = {}            # library id -> [paths] (after a scan)
        self.deny_create = None        # HTTP status POST /Library/VirtualFolders answers (an API key refused)
        self.drop_options = False      # the server ignores the LibraryOptions of a create
        self.embedded_override = False  # the server names items by embedded title whatever the option says

    def scan(self):
        self.items = {iid(f"{LOC}/{f}"): f"{LOC}/{f}" for f in self.disk}

    def add_vlib(self, name, location=None, ctype="homevideos", **opts):
        lid = hashlib.md5(f"lib:{name}".encode()).hexdigest()
        self.vlibs[name] = {"Name": name, "CollectionType": ctype, "ItemId": lid,
                            "Locations": [location or f"{DAYS_LOC}/{name}"],
                            "LibraryOptions": {**ev.DAY_OPTIONS, **opts}}
        return lid

    def day_lib(self, lid):
        return next((v for v in self.vlibs.values() if v["ItemId"] == lid), None)

    def lib_items(self, lid):
        """What GET /Items?ParentId= gives for a day library: name from the file name, or with
        'prefer embedded titles' on the embedded title (the plain exercise name); name sort."""
        v = self.day_lib(lid)
        embedded = v["LibraryOptions"].get("EnableEmbeddedTitles") or self.embedded_override
        out = []
        for path in self.day_items.get(lid, []):
            stem = Path(path).stem
            name = stem.split(" ", 1)[1] if embedded else stem
            out.append({"Id": iid(path), "Path": path, "Name": name, "SortName": name.lower(), "Type": "Video"})
        return sorted(out, key=lambda x: x["SortName"])

    def handle(self, method, url, headers, params=None, json=None):
        assert headers.get("Authorization") == f'MediaBrowser Token="{self.KEY}"'
        path = urlparse(url).path
        parts = [p for p in path.split("/") if p]
        params = params or {}
        if method != "GET":
            self.writes.append((method, path, json))
        if (method, path) == ("GET", "/Users"):
            return Resp(200, self.users)
        if (method, path) == ("GET", "/Library/VirtualFolders"):
            return Resp(200, [self.lib, {"Name": "Movies", "CollectionType": "movies", "ItemId": "b" * 32,
                                         "Locations": ["/data/media/movies"]}, *copy.deepcopy(list(self.vlibs.values()))])
        if (method, path) == ("POST", "/Library/VirtualFolders"):
            name, paths = params["name"], params["paths"]
            assert params["collectionType"] == "homevideos" and params["refreshLibrary"] == "false"
            assert isinstance(paths, list) and len(paths) == 1 and set(json) == {"LibraryOptions"}
            if self.deny_create:
                return Resp(self.deny_create, {"detail": "Forbidden"})
            if name in self.vlibs or name in ("Exercise", "Movies"):
                return Resp(400, {"detail": "A media library with that name already exists"})
            if not paths[0].startswith(DAYS_LOC + "/") or paths[0][len(DAYS_LOC) + 1:] not in self.day_disk:
                return Resp(400, {"detail": "The path does not exist"})
            lid = self.add_vlib(name, paths[0])
            self.vlibs[name]["LibraryOptions"] = {} if self.drop_options else copy.deepcopy(json["LibraryOptions"])
            return Resp(204)
        if (method, path) == ("DELETE", "/Library/VirtualFolders"):
            assert params["refreshLibrary"] == "false" and params["name"] in self.vlibs, params
            lid = self.vlibs.pop(params["name"])["ItemId"]
            self.day_items.pop(lid, None)
            return Resp(204)
        if (method, path) == ("POST", "/Library/VirtualFolders/LibraryOptions"):
            v = self.day_lib(json["Id"])
            assert v is not None, json
            v["LibraryOptions"] = copy.deepcopy(json["LibraryOptions"])
            return Resp(204)
        if method == "POST" and parts[0] == "Items" and parts[2:] == ["Refresh"]:
            assert params["Recursive"] == "true"
            if parts[1] == LIB:
                self.scan()
            else:
                v = self.day_lib(parts[1])
                assert v is not None, parts
                loc = v["Locations"][0]
                self.day_items[parts[1]] = [f"{loc}/{f}" for f in self.day_disk.get(Path(loc).name, [])]
            return Resp(204)
        if (method, path) == ("GET", "/Items"):
            if params.get("IncludeItemTypes") == "Playlist":
                return Resp(200, {"Items": [{"Id": k, "Name": p["name"]} for k, p in self.playlists.items()
                                            if self.visible(p, params["userId"])]})
            if self.day_lib(params["ParentId"]) is not None:
                assert params["SortBy"] == "SortName"
                return Resp(200, {"Items": self.lib_items(params["ParentId"])})
            assert params["ParentId"] == LIB
            return Resp(200, {"Items": [{"Id": k, "Path": v, "Type": "Video"} for k, v in sorted(self.items.items())]})
        if method == "GET" and parts[0] == "Items" and len(parts) == 2:
            uid = params.get("userId")
            assert uid, "GET /Items/{id} without userId"
            pl = self.playlists.get(parts[1])
            if pl is not None:
                return Resp(200, {"Id": parts[1]}) if self.visible(pl, uid) else Resp(404, {"detail": "nf"})
            return Resp(200, {"Id": parts[1]}) if parts[1] in self.items else Resp(404, {"detail": "nf"})
        if method == "POST" and path == "/Playlists":
            if not json.get("UserId"):
                return self.no_user()
            assert set(json["Ids"]) <= set(self.items)
            if json["Name"] in self.fail_create:
                self.fail_create.discard(json["Name"])
                return Resp(500, {"detail": "boom"})
            self.n += 1
            pid = f"{self.n:032x}"
            shares = list(json["Users"]) + ([{"UserId": self.share_on_create, "CanEdit": False}]
                                            if self.share_on_create else [])
            self.playlists[pid] = {"name": json["Name"], "owner": json["UserId"], "public": json["IsPublic"],
                                   "shares": shares, "ids": list(json["Ids"])}
            return Resp(200, {"Id": pid})
        if parts[0] == "Playlists":
            if method == "GET" and parts[2:] == ["Items"]:
                uid = params.get("userId")
                if not uid:
                    return self.no_user()
                pl = self.playlists.get(parts[1])
                if pl is None or not self.visible(pl, uid):
                    return Resp(404, {"detail": "Playlist not found"})
                return Resp(200, {"Items": [{"Id": i} for i in pl["ids"]]})
            return self.no_user()                   # GET /Playlists/{id}, /Users, update, remove, move
        if method == "DELETE" and parts[0] == "Items":
            if parts[1] in self.fail_delete:
                return Resp(500, {"detail": "delete boom"})
            if parts[1] not in self.playlists:
                return Resp(404, {"detail": "nf"})
            self.playlists.pop(parts[1])
            return Resp(204)
        raise AssertionError((method, path, params))


def _visible(self, pl, uid):
    return pl["owner"] == uid or pl["public"] or any(x["UserId"] == uid for x in pl["shares"])


def _no_user(self):
    return Resp(400, "Error processing request.")


FakeJellyfin.visible = _visible
FakeJellyfin.no_user = _no_user


class FakeSession:
    def __init__(self, fake):
        self.fake = fake
        self.headers = {}

    def request(self, method, url, timeout=None, params=None, json=None):
        return self.fake.handle(method, url, self.headers, params=params, json=json)


@pytest.fixture
def jf(monkeypatch):
    import requests
    f = FakeJellyfin(files=names())
    monkeypatch.setattr(requests, "Session", lambda: FakeSession(f))
    monkeypatch.setenv("JELLYFIN_API_KEY", FakeJellyfin.KEY)
    monkeypatch.setenv("JELLYFIN_URL", "https://jellyfin.test")
    return f


@pytest.fixture
def videos(tmp_path):
    p = tmp_path / "wger_videos.json"
    ev.write_videos(p, {}, [], ev.LINK_BASE)
    return p


def jrun(*args, videos):
    return ev.main(["jellyfin", *args], sleep=lambda s: None, videos_path=videos)


def scans_only(jf):
    """True when every write was a library scan (Exercise + the day libraries): an idempotent run."""
    return all(w[0] == "POST" and w[1].endswith("/Refresh") for w in jf.writes) and \
        [w[1] for w in jf.writes][:1] == [f"/Items/{LIB}/Refresh"]


def test_jellyfin_offline_dry_run_without_key(monkeypatch, videos, capsys):
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    assert jrun(videos=videos) == 0
    out = capsys.readouterr().out
    assert "offline dry run" in out and "Rachel · 2 Tue/Fri Cardio" in out
    assert jrun("--apply", videos=videos) == 2
    assert "JELLYFIN_API_KEY is not set" in capsys.readouterr().err


def test_jellyfin_dry_run_writes_nothing(jf, videos, capsys):
    before = videos.read_text()
    jf.scan()
    assert jrun(videos=videos) == 0
    assert jf.writes == [] and videos.read_text() == before
    out = capsys.readouterr().out
    assert "would: create playlist 'Jacob · 1 Mon Lower A'" in out and "would change" in out


def test_apply_scans_makes_private_ordered_playlists_and_writes_the_mapping(jf, videos, capsys):
    assert jrun("--apply", videos=videos) == 0
    by_name = {p["name"]: p for p in jf.playlists.values()}
    assert len(by_name) == 8
    item = {ev.wanted_files(PLAN)[Path(p).name]: i for i, p in jf.items.items()}
    for pl in ev.plan_playlists(PLAN):
        got = by_name[pl["name"]]
        assert got["ids"] == [item[k] for k in pl["keys"]], pl["name"]          # workout order
        assert got["public"] is False and got["shares"] == []
        assert got["owner"] == {"jmack": JMACK, "rach": RACH}[pl["login"]]
    doc = json.loads(videos.read_text())
    assert doc["base_url"] == "https://jellyfin.kmkdp.com"
    assert set(doc["items"]) == VIDEO_KEYS and "treadmill-walk" not in doc["items"]
    assert [len(p["ids"]) for p in jf.playlists.values() if p["owner"] == JMACK] == [9, 9, 9, 9]
    assert doc["items"]["plank"]["id"] == iid(f"{LOC}/{ev.file_name('plank', PLAN['exercises']['plank'])}")
    assert doc["items"]["plank"]["youtube"] == PLAN["exercises"]["plank"]["video"][-11:]
    assert len(doc["playlists"]) == 8


def test_shared_days_get_one_playlist(jf, videos):
    assert jrun("--apply", videos=videos) == 0
    rach = [p["name"] for p in jf.playlists.values() if p["owner"] == RACH]
    assert sorted(rach) == sorted(["Rachel · 1 Mon Strength A", "Rachel · 2 Tue/Fri Cardio",
                                   "Rachel · 3 Wed/Sat/Sun Daily parts", "Rachel · 4 Thu Strength B"])


def test_second_run_writes_nothing_but_the_scan(jf, videos, capsys):
    assert jrun("--apply", videos=videos) == 0
    first = videos.read_text()
    jf.writes.clear()
    assert jrun("--apply", videos=videos) == 0
    assert scans_only(jf) and len(jf.writes) == 1 + 8
    assert videos.read_text() == first and "unchanged" in capsys.readouterr().out


def test_item_ids_stay_put_for_an_unchanged_path(jf, videos):
    assert jrun("--apply", videos=videos) == 0
    ids = json.loads(videos.read_text())["items"]
    jf.scan()                                            # a rescan: same paths, same ids
    assert jrun("--apply", videos=videos) == 0
    assert json.loads(videos.read_text())["items"] == ids


def test_drifted_playlist_is_recreated_not_updated(jf, videos, capsys):
    assert jrun("--apply", videos=videos) == 0
    (pid, pl), = [(k, p) for k, p in jf.playlists.items() if p["name"] == "Jacob · 2 Wed Upper A"]
    want = list(pl["ids"])
    pl["ids"].reverse()
    jf.writes.clear()
    assert jrun("--apply", videos=videos) == 0
    assert "delete playlist 'Jacob · 2 Wed Upper A' (" + pid + ") to re-create it (order differ" in capsys.readouterr().out
    assert pid not in jf.playlists
    (new, npl), = [(k, p) for k, p in jf.playlists.items() if p["name"] == "Jacob · 2 Wed Upper A"]
    assert npl["ids"] == want and npl["owner"] == JMACK and not npl["public"] and npl["shares"] == []
    pw = [(w[0], w[1]) for w in jf.writes if not w[1].endswith("/Refresh")]
    assert pw == [("DELETE", f"/Items/{pid}"), ("POST", "/Playlists")]          # never POST /Playlists/{id}
    rec = next(x for x in json.loads(videos.read_text())["playlists"] if x["name"] == "Jacob · 2 Wed Upper A")
    assert rec["id"] == new


def test_never_calls_the_user_session_playlist_endpoints(jf, videos):
    assert jrun("--apply", videos=videos) == 0
    assert jrun("--apply", videos=videos) == 0
    assert not [w for w in jf.writes if w[1].startswith("/Playlists/")]


def test_a_new_video_in_the_plan_updates_the_mapping_and_playlist(jf, videos, tmp_path):
    assert jrun("--apply", videos=videos) == 0
    plan = copy.deepcopy(PLAN)
    plan["exercises"]["plank"]["video"] = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    jf.disk.add("plank [BBBBBBBBBBB].mp4")
    assert ev.main(["jellyfin", "--apply"], plan_path=p, sleep=lambda s: None, videos_path=videos) == 0
    doc = json.loads(videos.read_text())
    assert doc["items"]["plank"] == {"id": iid(f"{LOC}/plank [BBBBBBBBBBB].mp4"), "youtube": "BBBBBBBBBBB",
                                     "file": "plank [BBBBBBBBBBB].mp4"}
    mon = next(x for x in jf.playlists.values() if x["name"] == "Jacob · 1 Mon Lower A")
    assert doc["items"]["plank"]["id"] in mon["ids"]
    assert len(jf.playlists) == 8


def test_other_accounts_seeing_the_library_is_only_a_note(jf, videos, capsys):
    jf.users[2]["Policy"]["EnableAllFolders"] = True                   # guest: all libraries
    jf.users[3]["Policy"]["EnabledFolders"].append(LIB)                # admin: ticked
    assert jrun("--apply", videos=videos) == 0
    out = capsys.readouterr().out
    notes = [x for x in out.splitlines() if x.startswith("NOTE other accounts")]
    assert notes == ["NOTE other accounts can see the library (allowed; the playlists stay private): "
                     "guest (all libraries), kdkadmin (ticked)"]
    assert "ACCESS" not in out and len(jf.playlists) == 8
    for pl in jf.playlists.values():                                   # still private to the owner
        assert pl["public"] is False and pl["shares"] == [] and pl["owner"] in (JMACK, RACH)


def test_no_note_when_only_the_plan_people_see_it(jf, videos, capsys):
    assert jrun(videos=videos) == 0
    assert "NOTE other accounts" not in capsys.readouterr().out


@pytest.mark.parametrize("how", ["shared", "public"])
def test_a_playlist_another_user_can_see_is_a_hard_failure(jf, videos, capsys, how):
    assert jrun("--apply", videos=videos) == 0
    (pid, pl), = [(k, p) for k, p in jf.playlists.items() if p["name"] == "Jacob · 2 Wed Upper A"]
    if how == "shared":
        pl["shares"] = [{"UserId": OTHER, "CanEdit": False}]
    else:
        pl["public"] = True
    assert jrun("--apply", videos=videos) == 2
    err = capsys.readouterr().err
    assert f"playlist 'Jacob · 2 Wed Upper A' ({pid}) is visible to " in err
    assert f"guest (in their playlists; GET /Items/{pid} as them: HTTP 200)" in err
    if how == "public":
        assert "rach (" in err and "kdkadmin (" in err                     # everyone, the other plan person too
    else:
        assert "rach (" not in err
    assert pid in jf.playlists                                            # nothing deleted
    rec = [x["name"] for x in json.loads(videos.read_text())["playlists"]]
    assert "Jacob · 2 Wed Upper A" not in rec and "Jacob · 1 Mon Lower A" in rec  # only verified ones recorded


def test_a_new_playlist_the_server_shares_fails_and_is_not_recorded(jf, videos, capsys):
    jf.share_on_create = ADMIN
    assert jrun("--apply", videos=videos) == 2
    assert "is visible to kdkadmin (in their playlists; GET /Items/" in capsys.readouterr().err
    doc = json.loads(videos.read_text())
    assert len(doc["items"]) == len(VIDEO_KEYS) and doc["playlists"] == []


def test_a_failure_midway_writes_the_items_and_resumes(jf, videos, capsys):
    second = ev.plan_playlists(PLAN)[1]["name"]
    jf.fail_create = {second}
    assert jrun("--apply", videos=videos) == 2
    out, err = capsys.readouterr()
    assert "HTTP 500" in err and "wrote wger_videos.json before stopping: 46 item id(s), 1 verified" in out
    doc = json.loads(videos.read_text())
    assert set(doc["items"]) == VIDEO_KEYS                               # gen_wger.py can already run
    assert [p["name"] for p in doc["playlists"]] == ["Jacob · 1 Mon Lower A"]
    first_id = doc["playlists"][0]["id"]
    jf.writes.clear()
    assert jrun("--apply", videos=videos) == 0                           # resume
    doc = json.loads(videos.read_text())
    assert len(doc["playlists"]) == 8 and len(jf.playlists) == 8
    assert next(p for p in doc["playlists"] if p["name"] == "Jacob · 1 Mon Lower A")["id"] == first_id
    creates = [w for w in jf.writes if w[:2] == ("POST", "/Playlists")]
    assert len(creates) == 7 and not [w for w in jf.writes if w[0] == "DELETE"]


def test_resume_when_the_mapping_was_never_written(jf, videos):
    """The real first run: one playlist made, then the tool died; wger_videos.json still empty."""
    pl0 = ev.plan_playlists(PLAN)[0]
    jf.scan()
    item = {ev.wanted_files(PLAN)[Path(p).name]: i for i, p in jf.items.items()}
    jf.playlists["149d6a5fe1ad9dded863c4ade2095567"] = {"name": pl0["name"], "owner": JMACK, "public": False,
                                                        "shares": [], "ids": [item[k] for k in pl0["keys"]]}
    assert jrun("--apply", videos=videos) == 0
    doc = json.loads(videos.read_text())
    assert next(p for p in doc["playlists"] if p["name"] == pl0["name"])["id"] == "149d6a5fe1ad9dded863c4ade2095567"
    assert len(jf.playlists) == 8 and len(doc["items"]) == len(VIDEO_KEYS)


def test_plan_person_without_access_is_an_error(jf, videos, capsys):
    jf.users[1]["Policy"]["EnabledFolders"] = []
    assert jrun("--apply", videos=videos) == 2
    assert "rach cannot see the library" in capsys.readouterr().out and jf.writes == []


def test_missing_user_or_library_or_wrong_type(jf, videos, capsys):
    jf.users[0]["Name"] = "jacob"
    assert jrun(videos=videos) == 2
    assert "no Jellyfin user named ['jmack']" in capsys.readouterr().err
    jf.users[0]["Name"] = "jmack"
    jf.lib["CollectionType"] = "movies"
    assert jrun(videos=videos) == 2
    assert "must be Home videos" in capsys.readouterr().err


def test_missing_file_stops_apply_before_playlists(jf, videos, capsys):
    jf.disk.discard(names()[0])
    assert jrun("--apply", videos=videos) == 2
    assert jf.playlists == {} and "MISSING" in capsys.readouterr().out
    assert json.loads(videos.read_text())["items"] == {}


def test_playlists_the_tool_did_not_make_are_never_deleted(jf, videos, tmp_path, capsys):
    jf.scan()
    jf.playlists["f" * 32] = {"name": "Jacob favourites", "owner": JMACK, "public": False, "shares": [], "ids": []}
    assert jrun("--apply", "--prune", videos=videos) == 0
    assert "f" * 32 in jf.playlists


def test_prune_deletes_a_managed_playlist_that_left_the_plan(jf, videos, tmp_path, capsys):
    assert jrun("--apply", videos=videos) == 0
    plan = copy.deepcopy(PLAN)
    plan["people"]["jmack"]["routines"] = [r for r in plan["people"]["jmack"]["routines"] if r["day"] != "sat"]
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    run = lambda *a: ev.main(["jellyfin", *a], plan_path=p, sleep=lambda s: None, videos_path=videos)  # noqa: E731
    assert run("--apply") == 0                                            # no --prune: kept, still recorded
    assert any(x["name"] == "Jacob · 4 Sat Upper B" for x in jf.playlists.values())
    assert "EXTRA playlist 'Jacob · 4 Sat Upper B'" in capsys.readouterr().out
    assert any(x["name"] == "Jacob · 4 Sat Upper B" for x in json.loads(videos.read_text())["playlists"])
    assert run("--apply", "--prune") == 0
    assert not any(x["name"] == "Jacob · 4 Sat Upper B" for x in jf.playlists.values())
    assert not any(x["name"] == "Jacob · 4 Sat Upper B" for x in json.loads(videos.read_text())["playlists"])


def test_lost_mapping_adopts_playlists_by_name(jf, videos):
    assert jrun("--apply", videos=videos) == 0
    ids = set(jf.playlists)
    ev.write_videos(videos, {}, [], ev.LINK_BASE)
    assert jrun("--apply", videos=videos) == 0
    assert set(jf.playlists) == ids


def test_mapping_written_by_the_tool_is_what_gen_wger_reads(jf, videos):
    import gen_wger as gw
    assert jrun("--apply", videos=videos) == 0
    v = gw.load_videos(videos)
    assert gw.missing_videos(json.loads((TOOLS / "wger_plan.json").read_text()), v) == []
    url = gw.video_url("plank", PLAN["exercises"]["plank"], v)
    assert url == f"https://jellyfin.kmkdp.com/web/#/details?id={json.loads(videos.read_text())['items']['plank']['id']}"


# ---------------------------------------------------------------- 2026-11-14 rename to "<person> · <n> ..."
HAND = {"e" * 32: "Jacob favourites",                 # hand-made by jmack
        "d" * 32: "Jacob · Mon Lower A (copy)",       # merely starts like a generated name
        "c" * 32: "Jacob · 1 Mon Lower A copy"}


def seed_old(jf, videos, filled):
    """The server as on 2026-11-14: the 8 old-named playlists (private, right order) plus jmack's own
    hand-made ones; wger_videos.json empty (main before #517) or recording the 8 old ones."""
    jf.scan()
    item = {ev.wanted_files(PLAN)[Path(p).name]: i for i, p in jf.items.items()}
    rec = []
    for pl, pid in zip(ev.plan_playlists(PLAN), OLD_IDS):
        owner = {"jmack": JMACK, "rach": RACH}[pl["login"]]
        jf.playlists[pid] = {"name": pl["old_name"], "owner": owner, "public": False, "shares": [],
                             "ids": [item[k] for k in pl["keys"]]}
        rec.append({"login": pl["login"], "days": pl["days"], "name": pl["old_name"], "id": pid})
    for pid, name in HAND.items():
        jf.playlists[pid] = {"name": name, "owner": JMACK, "public": False, "shares": [], "ids": []}
    if filled:
        ev.write_videos(videos, {}, rec, ev.LINK_BASE)
    return item


def deletes(jf):
    return [w[1].rsplit("/", 1)[1] for w in jf.writes if w[0] == "DELETE"]


@pytest.mark.parametrize("filled", [False, True], ids=["empty-mapping", "filled-mapping"])
def test_old_named_playlists_are_replaced(jf, videos, capsys, filled):
    item = seed_old(jf, videos, filled)
    assert jrun("--apply", videos=videos) == 0
    new = {p["name"]: (k, p) for k, p in jf.playlists.items() if p["name"] not in HAND.values()}
    assert sorted(new) == sorted(p["name"] for p in ev.plan_playlists(PLAN))   # 8 new, 0 old
    assert not set(OLD_IDS) & set(jf.playlists)
    assert {k: jf.playlists[k]["name"] for k in HAND} == HAND                  # nothing else deleted
    assert sorted(deletes(jf)) == sorted(OLD_IDS)
    for pl in ev.plan_playlists(PLAN):
        k, got = new[pl["name"]]
        assert got["ids"] == [item[x] for x in pl["keys"]] and not got["public"] and got["shares"] == []
    # each old one is deleted only after its replacement was created
    seq = [(w[0], (w[2] or {}).get("Name") or w[1].rsplit("/", 1)[1]) for w in jf.writes if w[0] in ("POST", "DELETE")]
    for pl, oid in zip(ev.plan_playlists(PLAN), OLD_IDS):
        assert seq.index(("POST", pl["name"])) < seq.index(("DELETE", oid))
    doc = json.loads(videos.read_text())
    assert [p["name"] for p in doc["playlists"]] == [p["name"] for p in ev.plan_playlists(PLAN)]
    assert {p["id"] for p in doc["playlists"]} == {k for k, _ in new.values()}
    out = capsys.readouterr().out
    assert "did: delete playlist 'Jacob · Mon Lower A' (149d6a5fe1ad9dded863c4ade2095567) of jmack: replaced by " \
           "'Jacob · 1 Mon Lower A'" in out
    jf.writes.clear()                                                          # second run: no writes
    assert jrun("--apply", videos=videos) == 0
    assert scans_only(jf)
    assert "unchanged" in capsys.readouterr().out


def test_migration_dry_run_lists_every_create_and_delete(jf, videos, capsys):
    seed_old(jf, videos, filled=True)
    before, pls = videos.read_text(), dict(jf.playlists)
    assert jrun(videos=videos) == 0
    assert jf.writes == [] and videos.read_text() == before and jf.playlists.keys() == pls.keys()
    out = capsys.readouterr().out
    would = [x for x in out.splitlines() if x.startswith("would: ") and "playlist" in x]
    assert len(would) == 16
    for pl, oid in zip(ev.plan_playlists(PLAN), OLD_IDS):
        assert f"would: create playlist {pl['name']!r} for {pl['login']}" in out
        assert f"would: delete playlist {pl['old_name']!r} ({oid}) of {pl['login']}: replaced by {pl['name']!r}" in out
    assert not [x for x in would if "Jacob favourites" in x or "(copy)" in x or " copy'" in x]


def test_a_failed_delete_is_reported_and_the_next_run_retries(jf, videos, capsys):
    seed_old(jf, videos, filled=True)
    jf.fail_delete = {OLD_IDS[1]}
    assert jrun("--apply", videos=videos) == 1
    out = capsys.readouterr().out
    assert f"ERROR could not delete playlist 'Jacob · Wed Upper A' ({OLD_IDS[1]}) of jmack" in out
    assert f"did: delete playlist 'Jacob · Wed Upper A' ({OLD_IDS[1]}) of jmack: replaced by " \
           "'Jacob · 2 Wed Upper A' -- FAILED" in out
    assert "ERROR 1 old playlist(s) not removed" in out
    assert OLD_IDS[1] in jf.playlists and not (set(OLD_IDS) - {OLD_IDS[1]}) & set(jf.playlists)  # the rest went on
    doc = json.loads(videos.read_text())
    assert [p["name"] for p in doc["playlists"]] == [p["name"] for p in ev.plan_playlists(PLAN)]  # only verified ones
    assert OLD_IDS[1] not in {p["id"] for p in doc["playlists"]}
    jf.fail_delete.clear()
    jf.writes.clear()
    assert jrun("--apply", videos=videos) == 0                                 # found by its exact old name
    assert [(w[0], w[1]) for w in jf.writes if w[0] != "POST" or w[1] == "/Playlists"] == \
           [("DELETE", f"/Items/{OLD_IDS[1]}")]
    assert {k: jf.playlists[k]["name"] for k in HAND} == HAND and len(jf.playlists) == 8 + len(HAND)


def test_an_old_name_that_is_not_provably_theirs_is_never_deleted(jf, videos, capsys):
    """An exact old name in jmack's list, but rach owns it and shares it with him: not his."""
    jf.scan()
    jf.playlists["9" * 32] = {"name": "Jacob · Mon Lower A", "owner": RACH, "public": False,
                              "shares": [{"UserId": JMACK, "CanEdit": False}], "ids": []}
    jf.playlists["8" * 32] = {"name": "Jacob · Wed Upper A", "owner": OTHER, "public": False, "shares": [], "ids": []}
    assert jrun("--apply", videos=videos) == 1
    assert "9" * 32 in jf.playlists and "8" * 32 in jf.playlists and deletes(jf) == []
    out = capsys.readouterr().out
    assert f"WARN playlist 'Jacob · Mon Lower A' ({'9' * 32}) is visible to rach" in out
    assert len(json.loads(videos.read_text())["playlists"]) == 8


def test_a_recorded_playlist_renamed_by_hand_is_left_alone(jf, videos, capsys):
    seed_old(jf, videos, filled=True)
    jf.playlists[OLD_IDS[0]]["name"] = "My Monday"
    assert jrun("--apply", videos=videos) == 0
    assert jf.playlists[OLD_IDS[0]]["name"] == "My Monday" and OLD_IDS[0] not in deletes(jf)
    assert f"NOTE recorded playlist {OLD_IDS[0]} of jmack is now named 'My Monday'" in capsys.readouterr().out
    assert OLD_IDS[0] not in {p["id"] for p in json.loads(videos.read_text())["playlists"]}
