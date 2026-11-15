"""Day folders (hardlinks on the share) and day libraries (one Jellyfin library per day), owner
decision 2026-11-14 "option B": the Apple TV client (Neptune) plays neither a playlist nor a folder,
only a flat library sorted by name. Uses the fakes of test_exercise_videos.py."""
import copy
import json

import pytest

from test_exercise_videos import (DAYS_LOC, GOBLET, LIB, PLAN, FakeShell, dl, ev, jf, jrun,  # noqa: F401
                                  names, videos)

DAY_NAMES = ["Jacob · 1 Mon Lower A", "Jacob · 2 Wed Upper A", "Jacob · 3 Fri Lower B", "Jacob · 4 Sat Upper B",
             "Rachel · 1 Mon Strength A", "Rachel · 2 Tue-Fri Cardio", "Rachel · 3 Wed-Sat-Sun Daily parts",
             "Rachel · 4 Thu Strength B"]
DAYS = ev.DAYS_DIR
MON = "Jacob · 1 Mon Lower A"


def want_links(plan=PLAN):
    return {lib["name"]: sorted(x["name"] for x in lib["links"]) for lib in ev.day_libraries(plan)}


def plan_file(tmp_path, plan):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    return p


# ---------------------------------------------------------------- the plan side
def test_day_folders_follow_the_playlists_in_workout_order():
    libs = ev.day_libraries(PLAN)
    assert [lib["name"] for lib in libs] == DAY_NAMES
    for lib, pl in zip(libs, ev.plan_playlists(PLAN)):
        assert [x["key"] for x in lib["links"]] == pl["keys"] and lib["days"] == pl["days"]
        names_ = [x["name"] for x in lib["links"]]
        assert sorted(names_) == names_                                   # name sort = workout order
        assert [n[:3] for n in names_] == [f"{i:02d} " for i in range(1, len(names_) + 1)]
        assert all(x["source"] == ev.file_name(x["key"], PLAN["exercises"][x["key"]]) for x in lib["links"])
        for n in [lib["name"], *names_]:
            assert not set(n) & set('/\\:*?"<>|[]') and not n.startswith(".")
    mon = libs[0]["links"]
    assert len(mon) == 9 and mon[0]["name"] == "01 Dumbbell Goblet Squat.mp4"   # treadmill skipped, no gap
    assert all(len(lib["links"]) == 9 for lib in libs if lib["login"] == "jmack")
    assert not [x for lib in libs for x in lib["links"] if x["key"] == "treadmill-walk"]
    assert libs[5]["days"] == ["tue", "fri"] and libs[6]["days"] == ["wed", "sat", "sun"]   # shared days: one folder
    assert "05 Yoga exercise Cow-cat.mp4" in [x["name"] for x in libs[5]["links"]]


def test_safe_name():
    assert ev.safe_name("Yoga exercise: Cow-cat") == "Yoga exercise Cow-cat"
    assert ev.safe_name("Push-Ups | Incline") == "Push-Ups Incline"
    assert ev.safe_name("Rachel · 3 Wed/Sat/Sun Daily parts") == "Rachel · 3 Wed-Sat-Sun Daily parts"
    assert ev.safe_name("..a [b]\tc?.") == "a b c"
    assert ev.safe_name("Dumbbell farmer's carry") == "Dumbbell farmer's carry"
    for bad in ("", " . ", "?" * 3, "x" * 201):
        with pytest.raises(ev.VideoError):
            ev.safe_name(bad)


def test_day_path_never_leaves_the_days_dir():
    assert ev.day_path(DAYS, MON, "01 Plank.mp4") == f"{DAYS}/{MON}/01 Plank.mp4"
    for bad in ("..", ".", "a/b", ""):
        with pytest.raises(ev.VideoError):
            ev.day_path(DAYS, bad)


# ---------------------------------------------------------------- download: the links
def test_download_apply_hardlinks_every_day_and_a_second_run_writes_nothing(capsys):
    sh = FakeShell(files=names())
    assert dl("--apply", shell=sh) == 0
    assert sh.links() == want_links()
    for lib in ev.day_libraries(PLAN):                                    # a hardlink: the source's inode
        for x in lib["links"]:
            assert sh.days[lib["name"]][x["name"]] == ("f", sh.ino[x["source"]])
    lns = [c for c in sh.calls if c[0] == "ln"]
    assert len(lns) == sum(len(v) for v in want_links().values()) == 59
    assert lns[0] == ["ln", "-fT", "--", f"/mnt/media/exercise/{GOBLET}", f"{DAYS}/{MON}/01 Dumbbell Goblet Squat.mp4"]
    out = capsys.readouterr().out
    assert f"linked {MON}/01 Dumbbell Goblet Squat.mp4 <- {GOBLET}" in out and "59 new" in out
    sh.calls.clear()
    assert dl("--apply", shell=sh) == 0 and sh.writes() == []
    assert "links 59 ok, 0 new, 0 re-linked, 0 removed" in capsys.readouterr().out


def test_day_folders_dry_run_writes_nothing(capsys):
    sh = FakeShell(files=names(), days_exists=False)
    assert dl(shell=sh) == 0 and sh.writes() == [] and not sh.days_exists
    out = capsys.readouterr().out
    assert f"{DAYS} does not exist yet (created by --apply)" in out
    assert f"would link {MON}/01 Dumbbell Goblet Squat.mp4 <- {GOBLET}" in out and "59 new, 0 re-linked, 0 removed (planned)" in out
    assert dl("--apply", shell=sh) == 0 and sh.days_exists and sh.links() == want_links()
    assert ["mkdir", "-p", "--", DAYS] in sh.calls


def test_a_refetch_relinks_the_new_file(capsys):
    sh = FakeShell(files=names())
    assert dl("--apply", shell=sh) == 0
    old = sh.ino[GOBLET]
    sh.calls.clear()
    assert dl("--apply", "--refetch", "goblet-squat", shell=sh) == 0
    new = sh.ino[GOBLET]
    assert new != old and sh.days[MON]["01 Dumbbell Goblet Squat.mp4"] == ("f", new)
    i_mv = next(i for i, c in enumerate(sh.calls) if c[0] == "mv")
    lns = [(i, c) for i, c in enumerate(sh.calls) if c[0] == "ln"]
    assert [c[-1] for _, c in lns] == [f"{DAYS}/{MON}/01 Dumbbell Goblet Squat.mp4"] and lns[0][0] > i_mv
    assert "(the source was replaced: inode differs)" in capsys.readouterr().out


def test_a_changed_plan_video_relinks_and_prune_leaves_the_days_consistent(tmp_path):
    sh = FakeShell(files=names())
    assert dl("--apply", shell=sh) == 0
    plan = copy.deepcopy(PLAN)
    plan["exercises"]["plank"]["video"] = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
    p = plan_file(tmp_path, plan)
    assert ev.main(["download", "--apply", "--prune"], plan_path=p, shell=sh, sleep=lambda s: None) == 0
    assert "plank [BBBBBBBBBBB].mp4" in sh.files and not [f for f in sh.files if f.startswith("plank [3")]
    assert sh.days[MON]["07 Plank.mp4"] == ("f", sh.ino["plank [BBBBBBBBBBB].mp4"])
    assert sh.links() == want_links(plan)


def test_a_day_that_loses_an_exercise_loses_its_link_and_renumbers(tmp_path, capsys):
    sh = FakeShell(files=names())
    assert dl("--apply", shell=sh) == 0
    plan = copy.deepcopy(PLAN)
    mon = next(r for r in plan["people"]["jmack"]["routines"] if r["day"] == "mon")
    mon["slots"] = [x for x in mon["slots"] if x.get("exercise") != "db-rdl"]
    p = plan_file(tmp_path, plan)
    assert ev.main(["download", "--apply"], plan_path=p, shell=sh, sleep=lambda s: None) == 0   # no --prune needed
    assert sh.links()[MON] == want_links(plan)[MON] and len(sh.links()[MON]) == 8
    assert "02 Step-ups.mp4" in sh.links()[MON] and "02 Dumbbell Romanian Deadlift.mp4" not in sh.links()[MON]
    assert f"removed {MON}/02 Dumbbell Romanian Deadlift.mp4 (not in this day any more)" in capsys.readouterr().out


def test_foreign_things_in_the_days_dir_are_never_touched(capsys):
    days = {MON: {"notes.txt": ("f", "9"), "01 Dumbbell Goblet Squat.mp4": ("l", "8"), "sub": ("d", "7")},
            "Holiday clips": {"01 Beach.mp4": ("f", "6")}}
    sh = FakeShell(files=names(), days=days)
    sh.days_top["Rachel · 2 Tue-Fri Cardio"] = "l"               # a symlinked day folder: never entered
    sh.days_top["readme.md"] = "f"
    assert dl("--apply", "--prune", shell=sh) == 1
    assert sh.days[MON]["notes.txt"] == ("f", "9") and "sub" in sh.days[MON]
    assert sh.days[MON]["01 Dumbbell Goblet Squat.mp4"] == ("f", sh.ino[GOBLET])     # symlink -> hardlink
    assert sh.days["Holiday clips"] == {"01 Beach.mp4": ("f", "6")}                   # not a day name: OTHER
    assert not [c for c in sh.calls if c[0] != "find" and any("Tue-Fri" in a for a in c[1:])]
    out = capsys.readouterr().out
    assert f"OTHER {MON}/notes.txt" in out and "OTHER Holiday clips" in out and "OTHER readme.md" in out
    assert "WARN day folder 'Rachel · 2 Tue-Fri Cardio' is not a plain directory" in out
    assert "(a symlink)" in out


def test_an_extra_day_folder_is_removed_only_with_prune(capsys):
    old = "Jacob · 5 Sun Old day"
    sh = FakeShell(files=names(), days={old: {"01 Plank.mp4": ("f", "5")}})
    assert dl("--apply", shell=sh) == 0 and old in sh.days
    assert f"EXTRA day folder {old!r} (1 link(s)): no longer in the plan; --prune deletes it" in capsys.readouterr().out
    assert dl("--prune", shell=sh) == 0 and old in sh.days                       # dry run
    assert dl("--apply", "--prune", shell=sh) == 0 and old not in sh.days
    assert [c for c in sh.calls if c[0] == "rmdir"] == [["rmdir", "--", f"{DAYS}/{old}"]]
    keep = "Jacob · 6 Sun Other"
    sh = FakeShell(files=names(), days={keep: {"01 Plank.mp4": ("f", "5"), "mine.txt": ("f", "4")}})
    assert dl("--apply", "--prune", shell=sh) == 1
    assert sh.days[keep] == {"mine.txt": ("f", "4")} and not [c for c in sh.calls if c[0] == "rmdir"]


def test_a_missing_source_is_skipped_and_fails_the_run(capsys):
    sh = FakeShell(files=[n for n in names() if n != GOBLET], silent={"goblet-squat"})
    assert dl("--apply", shell=sh) == 1
    out = capsys.readouterr().out
    assert f"SKIP {MON}/01 Dumbbell Goblet Squat.mp4: source {GOBLET} is not on the share" in out
    assert "01 Dumbbell Goblet Squat.mp4" not in sh.links()[MON] and len(sh.links()[MON]) == 8


def test_days_dir_must_be_beside_the_library_dir(capsys):
    for bad in ("/mnt/media/exercise/days", "/mnt/media", "/mnt/media/exercise", "relative"):
        assert dl("--days-dir", bad, shell=FakeShell(files=names())) == 2
    assert "must be an absolute path beside --dir" in capsys.readouterr().err


# ---------------------------------------------------------------- Jellyfin: the day libraries
def test_day_libraries_are_created_scanned_verified_and_recorded(jf, videos, capsys):
    assert jrun("--apply", videos=videos) == 0
    assert sorted(jf.vlibs) == sorted(DAY_NAMES)
    creates = [w for w in jf.writes if w[:2] == ("POST", "/Library/VirtualFolders")]
    assert len(creates) == 8 and all(w[2] == {"LibraryOptions": ev.DAY_OPTIONS} for w in creates)
    for name, v in jf.vlibs.items():
        assert v["CollectionType"] == "homevideos" and v["Locations"] == [f"{DAYS_LOC}/{name}"]
        o = v["LibraryOptions"]
        assert o["EnableEmbeddedTitles"] is False and o["EnableRealtimeMonitor"] is False
        assert o["EnableTrickplayImageExtraction"] is False and o["EnableInternetProviders"] is False
        assert o["TypeOptions"][0]["MetadataFetchers"] == [] and o["TypeOptions"][0]["ImageFetchers"] == []
        assert [i["Name"] for i in jf.lib_items(v["ItemId"])] == [n[:-4] for n in want_links()[name]]
    doc = json.loads(videos.read_text())
    assert [x["name"] for x in doc["libraries"]] == sorted(DAY_NAMES)
    assert all(x["origin"] == "api" and x["id"] == jf.vlibs[x["name"]]["ItemId"] for x in doc["libraries"])
    out = capsys.readouterr().out
    assert f"ok library {MON!r}: 9 item(s) in workout order" in out
    assert "ok library 'Rachel · 3 Wed-Sat-Sun Daily parts': 4 item(s)" in out
    assert not [w for w in jf.writes if w[0] == "DELETE"]


def test_day_library_access_note_names_who_sees_it(jf, videos, capsys):
    for u in jf.users:
        if u["Name"] in ("jmack", "rach", "kdkadmin"):
            u["Policy"]["EnableAllFolders"] = True
    assert jrun("--apply", videos=videos) == 0
    out = capsys.readouterr().out
    assert f"NOTE library {MON!r} is visible to: jmack (all libraries), rach (all libraries), " \
           "kdkadmin (all libraries)" in out
    assert "cannot see library" not in out


def test_day_library_owner_without_access_is_warned(jf, videos, capsys):
    assert jrun("--apply", videos=videos) == 0                    # fake jmack/rach: Exercise ticked only
    assert f"WARN jmack cannot see library {MON!r}" in capsys.readouterr().out


def test_day_libraries_dry_run_writes_nothing(jf, videos, capsys):
    jf.scan()
    before = videos.read_text()
    assert jrun(videos=videos) == 0
    assert jf.writes == [] and jf.vlibs == {} and videos.read_text() == before
    assert f"would: create library {MON!r} (Home videos, {DAYS_LOC}/{MON})" in capsys.readouterr().out


def test_offline_dry_run_lists_the_day_libraries(monkeypatch, videos, capsys):
    monkeypatch.delenv("JELLYFIN_API_KEY", raising=False)
    assert jrun(videos=videos) == 0
    assert f"  {MON} -> {DAYS_LOC}/{MON}: 01 Dumbbell Goblet Squat.mp4, " in capsys.readouterr().out


def test_refused_create_prints_hand_steps_then_adopts_the_hand_made_library(jf, videos, capsys):
    jf.deny_create = 403
    assert jrun("--apply", videos=videos) == 2                   # no day library, so no item ids: stops
    out = capsys.readouterr().out
    assert "HAND STEPS" in out
    assert f"Display name {MON!r} | Content type Home videos and photos | Folder {DAYS_LOC}/{MON}" in out
    assert "'Prefer embedded titles over filenames' OFF" in out and "ERROR could not create library" in out
    assert jf.vlibs == {} and json.loads(videos.read_text())["libraries"] == [] and jf.playlists == {}
    for name in DAY_NAMES:                                       # the owner makes them by hand
        jf.add_vlib(name)
    assert jrun("--apply", videos=videos) == 0
    doc = json.loads(videos.read_text())
    assert len(doc["libraries"]) == 8 and {x["origin"] for x in doc["libraries"]} == {"hand"}
    assert "ok library 'Rachel · 4 Thu Strength B': 7 item(s)" in capsys.readouterr().out


def test_embedded_titles_on_is_re_applied_off(jf, videos, capsys):
    lid = jf.add_vlib(MON, EnableEmbeddedTitles=True, EnableRealtimeMonitor=True)
    assert jrun("--apply", videos=videos) == 0
    assert jf.vlibs[MON]["LibraryOptions"]["EnableEmbeddedTitles"] is False
    upd = [w for w in jf.writes if w[1] == "/Library/VirtualFolders/LibraryOptions"]
    assert len(upd) == 1 and upd[0][2]["Id"] == lid
    assert "EnableRealtimeMonitor, EnableEmbeddedTitles" in capsys.readouterr().out
    assert next(x for x in json.loads(videos.read_text())["libraries"] if x["name"] == MON)["origin"] == "hand"


def test_wrong_order_or_count_fails_the_verification(jf, videos, capsys):
    jf.embedded_override = True                                  # items named by embedded title: alphabetical
    jf.day_disk["Rachel · 2 Tue-Fri Cardio"].pop()               # one link missing on the share
    assert jrun("--apply", videos=videos) == 1
    out = capsys.readouterr().out
    assert f"library {MON!r}: 9 item(s) in name order, not the day's 9 file(s) in workout order (the right files, " \
           "wrong order: is 'Prefer embedded titles' on?)" in out
    assert "library 'Rachel · 2 Tue-Fri Cardio': 4 item(s) in name order, not the day's 5" in out


def test_a_created_library_without_the_options_is_a_problem(jf, videos, capsys):
    jf.drop_options = True
    assert jrun("--apply", videos=videos) == 1
    assert "was created without the options" in capsys.readouterr().out


def test_a_same_named_library_of_another_kind_is_left_alone(jf, videos, capsys):
    jf.add_vlib(MON, location="/data/media/movies")
    jf.add_vlib("Rachel · 1 Mon Strength A", ctype="movies")
    before = copy.deepcopy(jf.vlibs)
    assert jrun("--apply", "--prune", videos=videos) == 2       # their days' videos have no item id: stops
    assert {k: jf.vlibs[k] for k in before} == before
    ids = {v["ItemId"] for v in before.values()}
    assert not [w for w in jf.writes if (w[2] or {}).get("Id") in ids or any(i in w[1] for i in ids)]
    assert "left alone" in capsys.readouterr().out
    assert len(jf.vlibs) == 8


def test_prune_deletes_only_libraries_the_tool_created(jf, videos, tmp_path, capsys):
    assert jrun("--apply", videos=videos) == 0
    jf.add_vlib("Kids")                                          # someone else's library
    hand = jf.add_vlib("Jacob · 7 Sun Hand")
    jf.add_vlib("Jacob · 8 Sun Reborn")
    plan = copy.deepcopy(PLAN)
    plan["people"]["jmack"]["routines"] = [r for r in plan["people"]["jmack"]["routines"] if r["day"] != "sat"]
    p = plan_file(tmp_path, plan)
    doc = json.loads(videos.read_text())                         # forged records: none of these may be deleted
    doc["libraries"] += [{"login": "jmack", "days": ["sun"], "name": "Exercise", "id": LIB, "origin": "api"},
                         {"login": "jmack", "days": ["sun"], "name": "Movies", "id": "b" * 32, "origin": "api"},
                         {"login": "jmack", "days": ["sun"], "name": "Jacob · 7 Sun Hand", "id": hand, "origin": "hand"},
                         {"login": "jmack", "days": ["sun"], "name": "Jacob · 8 Sun Reborn", "id": "0" * 32,
                          "origin": "api"}]
    videos.write_text(json.dumps(doc))
    run = lambda *a: ev.main(["jellyfin", *a], plan_path=p, sleep=lambda s: None, videos_path=videos)  # noqa: E731
    assert run("--apply") == 0
    assert "Jacob · 4 Sat Upper B" in jf.vlibs
    out = capsys.readouterr().out
    assert "EXTRA library 'Jacob · 4 Sat Upper B' is no longer in the plan; --prune deletes it" in out
    assert "library 'Jacob · 8 Sun Reborn' no longer has the recorded id" in out      # not ours: record dropped
    jf.writes.clear()
    assert run("--apply", "--prune") == 0
    dels = [w for w in jf.writes if w[0] == "DELETE" and w[1].startswith("/Library/")]   # (+ the Sat playlist)
    assert [w[1] for w in dels] == ["/Library/VirtualFolders"]
    assert "Jacob · 4 Sat Upper B" not in jf.vlibs
    assert {"Kids", "Jacob · 7 Sun Hand", "Jacob · 8 Sun Reborn"} <= set(jf.vlibs)
    out = capsys.readouterr().out
    assert "EXTRA library 'Jacob · 7 Sun Hand' was made by hand" in out
    assert "EXTRA library 'Exercise' is not a day-library name the tool may delete" in out
    assert "EXTRA library 'Movies' is not a day-library name the tool may delete" in out
    left = {x["name"] for x in json.loads(videos.read_text())["libraries"]}
    assert "Jacob · 4 Sat Upper B" not in left and "Jacob · 7 Sun Hand" in left
    assert "Jacob · 8 Sun Reborn" not in left


def test_a_recorded_library_deleted_by_hand_is_created_again(jf, videos, capsys):
    assert jrun("--apply", videos=videos) == 0
    old = jf.vlibs.pop(MON)["ItemId"]
    assert jrun("--apply", videos=videos) == 0
    assert MON in jf.vlibs and f"NOTE recorded library {MON!r} ({old}) is gone" in capsys.readouterr().out
