"""exercise_videos.py against a fake shell (download) and an in-memory fake Jellyfin 12 API (jellyfin).

The fake Jellyfin keeps users with library policies, one "Exercise" Home videos library, a
"disk" of files that a library refresh turns into items (id = md5 of the path, as stable as
Jellyfin's), and playlists with owner, OpenAccess, shares and ordered entries."""
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
LOC = "/data/media/exercise"
LIB = "a" * 32
JMACK, RACH, OTHER, ADMIN = "1" * 32, "2" * 32, "3" * 32, "4" * 32


def names(plan=PLAN):
    return sorted(ev.wanted_files(plan))


# ---------------------------------------------------------------- the plan side
def test_file_names_come_from_key_and_youtube_id_only():
    ex = PLAN["exercises"]["plank"]
    assert ev.file_name("plank", ex) == f"plank [{ex['video'][-11:]}].mp4"
    assert ev.FILE_RE.match(ev.file_name("plank", ex))
    renamed = {**ex, "name": "Something else entirely"}
    assert ev.file_name("plank", renamed) == ev.file_name("plank", ex)
    assert len(names()) == len(PLAN["exercises"])


def test_playlists_one_per_person_and_day_in_workout_order():
    pls = {p["name"]: p for p in ev.plan_playlists(PLAN)}
    assert list(pls) == ["Jacob · Mon Lower A", "Jacob · Wed Upper A", "Jacob · Fri Lower B", "Jacob · Sat Upper B",
                         "Rachel · Mon Strength A", "Rachel · Tue/Fri Cardio", "Rachel · Wed/Sat/Sun Daily parts",
                         "Rachel · Thu Strength B"]
    mon = pls["Jacob · Mon Lower A"]
    assert mon["login"] == "jmack" and mon["days"] == ["mon"]
    assert mon["keys"][:3] == ["treadmill-walk", "goblet-squat", "db-rdl"] and mon["keys"][-1] == "bird-dog"
    assert pls["Rachel · Mon Strength A"]["keys"] == ["double-calf-raise", "hip-flexor-stretch", "box-squat",
                                                       "pallof-press", "incline-push-up", "wall-squat", "cow-cat"]
    assert pls["Rachel · Wed/Sat/Sun Daily parts"]["days"] == ["wed", "sat", "sun"]


def test_bad_plan_key_or_video_is_rejected(tmp_path):
    plan = copy.deepcopy(PLAN)
    plan["exercises"]["Bad Key"] = plan["exercises"].pop("plank")
    plan["exercises"]["walking"]["video"] = "https://youtu.be/x"
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    with pytest.raises(ev.VideoError) as e:
        ev.load_plan(p)
    assert "Bad Key" in str(e.value) and "exercises.walking: video" in str(e.value)


def test_image_is_pinned_by_digest():
    assert "@sha256:" in ev.image_ref()


# ---------------------------------------------------------------- download (fake shell)
class FakeShell:
    """Plays find/test/mkdir/rm/docker against a dict-based directory and its .incoming temp dir.

    fail: {plan key: number of failing attempts} (-1 = always) -> yt-dlp exits 1 with YouTube's 403
    and leaves a .part file in .incoming. silent: keys whose run exits 0 without a file."""

    TMP = f"/mnt/media/exercise/{ev.INCOMING}"

    def __init__(self, files=None, exists=True, fail=None, silent=(), incoming=None, link=False):
        self.files = set(files or [])
        self.exists = exists
        self.calls = []
        self.fail = dict(fail or {})
        self.silent = set(silent)
        self.incoming = None if incoming is None else set(incoming)     # None = no temp dir
        self.link = link

    def __call__(self, argv, check=True):
        self.calls.append(argv)
        rc, out, err = 0, "", ""
        tmp = len(argv) > 1 and argv[-1] == self.TMP
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
        elif argv[0] == "test":
            rc = 0 if self.exists else 1
        elif argv[0] == "mkdir":
            self.exists = True
        elif argv[0] == "rm" and argv[1] == "-rf":
            assert argv[-1] == self.TMP, argv                          # never anything but the temp dir
            self.incoming = None
        elif argv[0] == "rm":
            self.files.discard(Path(argv[-1]).name)
        elif "run" in argv:
            out_tpl, url = argv[-2], argv[-1]
            name = out_tpl.replace("%(id)s", url[-11:]).replace("%(ext)s", "mp4")
            key = name.split(" ")[0]
            self.incoming = self.incoming if self.incoming is not None else set()
            if self.fail.get(key, 0):
                self.fail[key] -= 1
                self.incoming.add(name.replace(".mp4", ".f137.mp4.part"))
                rc, err = 1, "[youtube] x: Downloading\nERROR: unable to download video data: HTTP Error 403: Forbidden\n"
            elif key not in self.silent:
                self.incoming = {x for x in self.incoming if not x.startswith(key + " ")}
                self.files.add(name)
        else:
            raise AssertionError(argv)
        p = subprocess.CompletedProcess(argv, rc, out, err)
        if check and rc:
            raise ev.VideoError("failed")
        return p

    def writes(self):
        return [c for c in self.calls if c[0] in ("mkdir", "rm") or "run" in c]

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
    assert f"{len(PLAN['exercises'])} missing" in out and "dry run:" in out


def test_download_apply_fetches_only_missing_files_as_1000_with_the_pinned_image(capsys):
    have = names()[:5]
    sh = FakeShell(files=have)
    assert dl("--apply", shell=sh) == 0
    runs = [c for c in sh.calls if "run" in c]
    assert len(runs) == len(PLAN["exercises"]) - 5
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

    def scan(self):
        self.items = {iid(f"{LOC}/{f}"): f"{LOC}/{f}" for f in self.disk}

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
                                         "Locations": ["/data/media/movies"]}])
        if method == "POST" and parts[0] == "Items" and parts[2:] == ["Refresh"]:
            assert parts[1] == LIB and params["Recursive"] == "true"
            self.scan()
            return Resp(204)
        if (method, path) == ("GET", "/Items"):
            if params.get("IncludeItemTypes") == "Playlist":
                return Resp(200, {"Items": [{"Id": k, "Name": p["name"]} for k, p in self.playlists.items()
                                            if p["owner"] == params["userId"] or p["public"]]})
            assert params["ParentId"] == LIB
            return Resp(200, {"Items": [{"Id": k, "Path": v, "Type": "Video"} for k, v in sorted(self.items.items())]})
        if method == "POST" and path == "/Playlists":
            assert set(json["Ids"]) <= set(self.items)
            self.n += 1
            pid = f"{self.n:032x}"
            self.playlists[pid] = {"name": json["Name"], "owner": json["UserId"], "public": json["IsPublic"],
                                   "shares": json["Users"], "ids": list(json["Ids"])}
            return Resp(200, {"Id": pid})
        if parts[0] == "Playlists":
            pl = self.playlists.get(parts[1])
            if pl is None:
                return Resp(404, {"detail": "nf"})
            if method == "GET" and len(parts) == 2:
                return Resp(200, {"OpenAccess": pl["public"], "Shares": pl["shares"], "ItemIds": pl["ids"]})
            if method == "GET" and parts[2:] == ["Items"]:
                return Resp(200, {"Items": [{"Id": i} for i in pl["ids"]]})
            if method == "POST" and len(parts) == 2:
                pl.update(name=json["Name"], ids=list(json["Ids"]), public=json["IsPublic"], shares=json["Users"])
                return Resp(204)
        if method == "DELETE" and parts[0] == "Items":
            self.playlists.pop(parts[1])
            return Resp(204)
        raise AssertionError((method, path, params))


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


def test_jellyfin_offline_dry_run_without_key(monkeypatch, videos, capsys):
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    assert jrun(videos=videos) == 0
    out = capsys.readouterr().out
    assert "offline dry run" in out and "Rachel · Tue/Fri Cardio" in out
    assert jrun("--apply", videos=videos) == 2
    assert "JELLYFIN_API_KEY is not set" in capsys.readouterr().err


def test_jellyfin_dry_run_writes_nothing(jf, videos, capsys):
    before = videos.read_text()
    jf.scan()
    assert jrun(videos=videos) == 0
    assert jf.writes == [] and videos.read_text() == before
    out = capsys.readouterr().out
    assert "would: create playlist 'Jacob · Mon Lower A'" in out and "would change" in out


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
    assert set(doc["items"]) == set(PLAN["exercises"])
    assert doc["items"]["plank"]["id"] == iid(f"{LOC}/{ev.file_name('plank', PLAN['exercises']['plank'])}")
    assert doc["items"]["plank"]["youtube"] == PLAN["exercises"]["plank"]["video"][-11:]
    assert len(doc["playlists"]) == 8


def test_shared_days_get_one_playlist(jf, videos):
    assert jrun("--apply", videos=videos) == 0
    rach = [p["name"] for p in jf.playlists.values() if p["owner"] == RACH]
    assert sorted(rach) == sorted(["Rachel · Mon Strength A", "Rachel · Tue/Fri Cardio",
                                   "Rachel · Wed/Sat/Sun Daily parts", "Rachel · Thu Strength B"])


def test_second_run_writes_nothing_but_the_scan(jf, videos, capsys):
    assert jrun("--apply", videos=videos) == 0
    first = videos.read_text()
    jf.writes.clear()
    assert jrun("--apply", videos=videos) == 0
    assert [w[1] for w in jf.writes] == [f"/Items/{LIB}/Refresh"]
    assert videos.read_text() == first and "unchanged" in capsys.readouterr().out


def test_item_ids_stay_put_for_an_unchanged_path(jf, videos):
    assert jrun("--apply", videos=videos) == 0
    ids = json.loads(videos.read_text())["items"]
    jf.scan()                                            # a rescan: same paths, same ids
    assert jrun("--apply", videos=videos) == 0
    assert json.loads(videos.read_text())["items"] == ids


def test_drifted_playlist_is_put_back_in_order_and_private(jf, videos, capsys):
    assert jrun("--apply", videos=videos) == 0
    (pid, pl), = [(k, p) for k, p in jf.playlists.items() if p["name"] == "Jacob · Wed Upper A"]
    pl["ids"].reverse()
    pl["public"] = True
    pl["shares"] = [{"UserId": OTHER, "CanEdit": False}]
    jf.writes.clear()
    assert jrun("--apply", videos=videos) == 0
    assert "update playlist 'Jacob · Wed Upper A' (order, public, shares)" in capsys.readouterr().out
    assert pl["public"] is False and pl["shares"] == [] and pl["ids"][0] != pl["ids"][-1]
    assert [w[1] for w in jf.writes if w[1].startswith("/Playlists")] == [f"/Playlists/{pid}"]


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
    mon = next(x for x in jf.playlists.values() if x["name"] == "Jacob · Mon Lower A")
    assert doc["items"]["plank"]["id"] in mon["ids"]
    assert len(jf.playlists) == 8


def test_another_account_with_access_stops_everything(jf, videos, capsys):
    jf.users[2]["Policy"]["EnableAllFolders"] = True
    assert jrun("--apply", videos=videos) == 2
    out, err = capsys.readouterr()
    assert "ACCESS guest can see the library ('Enable access to all libraries' is on)" in out
    assert jf.writes == [] and "nothing written" in err
    jf.users[2]["Policy"]["EnableAllFolders"] = False
    jf.users[3]["Policy"]["EnabledFolders"].append(LIB)                # an admin is not exempt
    assert jrun(videos=videos) == 2
    assert "ACCESS kdkadmin can see the library (the library is ticked)" in capsys.readouterr().out


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
    assert any(x["name"] == "Jacob · Sat Upper B" for x in jf.playlists.values())
    assert "EXTRA playlist 'Jacob · Sat Upper B'" in capsys.readouterr().out
    assert any(x["name"] == "Jacob · Sat Upper B" for x in json.loads(videos.read_text())["playlists"])
    assert run("--apply", "--prune") == 0
    assert not any(x["name"] == "Jacob · Sat Upper B" for x in jf.playlists.values())
    assert not any(x["name"] == "Jacob · Sat Upper B" for x in json.loads(videos.read_text())["playlists"])


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
