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
    """Plays find/test/mkdir/rm/docker against a dict-based directory."""

    def __init__(self, files=None, exists=True, fail_download=()):
        self.files = set(files or [])
        self.exists = exists
        self.calls = []
        self.fail = set(fail_download)

    def __call__(self, argv, check=True):
        self.calls.append(argv)
        rc, out = 0, ""
        if argv[0] == "find":
            if not self.exists:
                rc = 1
            out = "".join(f + "\n" for f in sorted(self.files))
        elif argv[0] == "test":
            rc = 0 if self.exists else 1
        elif argv[0] == "mkdir":
            self.exists = True
        elif argv[0] == "rm":
            self.files.discard(Path(argv[-1]).name)
        elif "run" in argv:
            out_tpl, url = argv[-2], argv[-1]
            name = out_tpl.replace("%(id)s", url[-11:]).replace("%(ext)s", "mp4")
            if name.split(" ")[0] not in self.fail:
                self.files.add(name)
        else:
            raise AssertionError(argv)
        p = subprocess.CompletedProcess(argv, rc, out, "")
        if check and rc:
            raise ev.VideoError("failed")
        return p

    def writes(self):
        return [c for c in self.calls if c[0] in ("mkdir", "rm") or "run" in c]


def dl(*args, shell):
    return ev.main(["download", *args], shell=shell)


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
    sh = FakeShell(fail_download={"plank"})
    assert dl("--apply", shell=sh) == 1
    assert "not created: plank [" in capsys.readouterr().out


def test_prune_only_deletes_plan_pattern_files_and_only_with_apply(capsys):
    old = "old-move [AAAAAAAAAAA].mp4"
    foreign = ["notes.txt", "Some Movie (2020).mp4", ".incoming"]
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
