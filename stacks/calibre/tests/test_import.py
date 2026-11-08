"""stacks/calibre/importer.py: each source file reaches Calibre once.

Regression for 2026-11-08: the old `calibredb add -r --automerge ignore` loop
re-added the Pugh PDF (junk embedded title), the SRE epub (different author
string) and two formatless books made from mismatched LazyLibrarian .opf
sidecars on every 15-minute pass. calibredb itself is faked here: a stub
executable records each call and what was staged for it.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("calibre_importer", STACK / "importer.py")
imp = importlib.util.module_from_spec(spec)
sys.modules["calibre_importer"] = imp
spec.loader.exec_module(imp)

FAKE_CALIBREDB = r'''#!/usr/bin/env python3
import json, os, sys
book = sys.argv[-1]
calls = os.environ["FAKE_CALLS"]
entry = {"argv": sys.argv[1:], "staged": {}}
for name in sorted(os.listdir(book)):
    p = os.path.join(book, name)
    entry["staged"][name] = os.readlink(p) if os.path.islink(p) else None
with open(calls, "a") as f:
    f.write(json.dumps(entry) + "\n")
n = sum(1 for _ in open(calls))
mode = os.environ.get("FAKE_MODE", "added")
if mode == "fail":
    print("Forbidden"); sys.exit(1)
if mode == "duplicate":
    print("The following books were not added as they already exist in the database "
          "(see --duplicates option or --automerge option):", file=sys.stderr)
    print("  Some Title", file=sys.stderr); sys.exit(0)
print(f"Added book ids: {100 + n}")
'''


@pytest.fixture
def lab(tmp_path, monkeypatch):
    incoming, state, library = tmp_path / "incoming", tmp_path / "state", tmp_path / "library"
    for d in (incoming, state, library):
        d.mkdir()
    fake = tmp_path / "calibredb"
    fake.write_text(FAKE_CALIBREDB)
    fake.chmod(0o755)
    calls_path = tmp_path / "calls.jsonl"
    calls_path.touch()
    monkeypatch.setenv("FAKE_CALLS", str(calls_path))
    cfg = imp.Config(server="http://localhost:8081/", incoming=incoming, state_dir=state,
                     library=library, stage_dir=tmp_path / "stage",
                     heartbeat=tmp_path / "heartbeat", interval=1, calibredb=str(fake))

    class Lab:
        pass

    lab = Lab()
    lab.cfg, lab.incoming, lab.library, lab.state = cfg, incoming, library, state
    lab.index = imp.LibraryIndex(library)

    def write(rel, data=b"x"):
        p = incoming / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def run(index=None):
        m = imp.Manifest.load(state / "manifest.json")
        return imp.run_pass(cfg, m, index or lab.index)

    def calls():
        return [json.loads(line) for line in lab.calls_path.read_text().splitlines()]

    lab.write, lab.run, lab.calls, lab.calls_path = write, run, calls, calls_path
    return lab


def test_added_once_then_skipped(lab):
    lab.write("Jon Bodner/Learning Go/Learning Go.epub", b"go" * 100)
    s1 = lab.run()
    assert (s1.added, s1.skipped) == (1, 0)
    s2 = lab.run()
    assert (s2.added, s2.skipped, s2.failed) == (0, 1, 0)
    assert len(lab.calls()) == 1
    entry = json.loads((lab.state / "manifest.json").read_text())["files"]
    assert entry["Jon Bodner/Learning Go/Learning Go.epub"]["status"] == "added"
    assert entry["Jon Bodner/Learning Go/Learning Go.epub"]["ids"] == [101]


def test_lone_opf_is_never_a_book(lab):
    lab.write("Someone/Only Metadata/Only Metadata.opf", b"<package/>")
    lab.write("Someone/Only Metadata/cover.jpg", b"jpg")
    s = lab.run()
    assert s.line() == "added=0 merged=0 duplicate=0 in_library=0 skipped=0 failed=0"
    assert lab.calls() == []


def test_mismatched_sidecar_is_staged_under_the_ebook_basename(lab):
    # Hidalgo: 9781492076810.OReilly...opf next to 9781492076810.O'Reilly...epub
    d = "Alex Hidalgo/Implementing Service Level Objectives/"
    epub = lab.write(d + "9781492076810.O'Reilly.Implementing_SLOs.epub", b"slo" * 50)
    opf = lab.write(d + "9781492076810.OReilly.Implementing_SLOs.opf", b"<package/>")
    cover = lab.write(d + "cover.jpg", b"jpg")
    assert lab.run().added == 1
    (call,) = lab.calls()
    stem = epub.stem
    assert call["staged"] == {f"{stem}.epub": str(epub), f"{stem}.opf": str(opf),
                              "cover.jpg": str(cover)}
    argv = call["argv"]
    assert argv[:5] == ["add", "--with-library", "http://localhost:8081/", "--automerge", "ignore"]
    assert "--one-book-per-directory" in argv and "-r" not in argv
    assert not lab.cfg.stage_dir.exists()  # staging cleaned up


def test_ambiguous_sidecar_is_ignored(lab):
    lab.write("A/Two Books/one.epub", b"1" * 10)
    lab.write("A/Two Books/two.epub", b"2" * 10)
    lab.write("A/Two Books/whatever.opf", b"<package/>")
    assert lab.run().added == 2
    for call in lab.calls():
        assert not any(name.endswith(".opf") for name in call["staged"])


def test_formats_with_one_basename_are_one_book(lab):
    d = "Andrew Hunt/Pragmatic/"
    for ext in ("epub", "mobi", "pdf"):
        lab.write(d + f"The Pragmatic Programmer.{ext}", ext.encode() * 20)
    lab.write(d + "The Pragmatic Programmer.opf", b"<package/>")
    lab.write(d + "The Pragmatic Programmer.jpg", b"jpg")
    assert lab.run().added == 1
    (call,) = lab.calls()
    assert sorted(call["staged"]) == ["The Pragmatic Programmer." + e
                                      for e in ("epub", "jpg", "mobi", "opf", "pdf")]


def test_changed_file_is_re_added_touched_file_is_not(lab):
    p = lab.write("K/Book/Book.epub", b"v1")
    assert lab.run().added == 1
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 10**9))  # touch only
    s = lab.run()
    assert (s.added, s.skipped) == (0, 1)
    p.write_bytes(b"version two, longer")  # new size and content
    s = lab.run()
    assert s.added == 1
    assert len(lab.calls()) == 2


def test_manifest_survives_restart(lab):
    lab.write("K/Book/Book.pdf", b"%PDF" * 10)
    assert lab.run().added == 1
    # a "restart": fresh process state, only the files on disk remain
    s = imp.run_pass(lab.cfg, imp.Manifest.load(lab.state / "manifest.json"),
                     imp.LibraryIndex(lab.library))
    assert (s.added, s.skipped) == (0, 1)
    assert len(lab.calls()) == 1


def test_bytes_already_in_library_are_recorded_not_added(lab):
    # Pugh: same PDF as library id 3, but the embedded title is "0321719492.pdf"
    data = b"%PDF pugh" * 100
    lab.write("Ken Pugh/Lean-Agile/Lean-Agile ATDD - K. Pugh (Pearson, 2011) WW.pdf", data)
    lib = lab.library / "Kenneth Pugh" / "Lean-Agile (3)"
    lib.mkdir(parents=True)
    (lib / "Lean-Agile - Kenneth Pugh.pdf").write_bytes(data)
    (lib / "metadata.opf").write_bytes(b"<package/>")
    s = lab.run()
    assert (s.in_library, s.added) == (1, 0)
    assert lab.calls() == []
    assert lab.run().skipped == 1


def test_same_size_different_bytes_is_added(lab):
    lab.write("A/B/B.epub", b"aaaa")
    (lab.library / "X").mkdir()
    (lab.library / "X" / "x.epub").write_bytes(b"bbbb")
    assert lab.run().added == 1


def test_failed_add_is_retried_next_pass(lab, monkeypatch):
    lab.write("A/B/B.epub", b"data")
    monkeypatch.setenv("FAKE_MODE", "fail")
    assert lab.run().failed == 1
    assert json.loads((lab.state / "manifest.json").read_text())["files"] == {}
    monkeypatch.setenv("FAKE_MODE", "added")
    assert lab.run().added == 1


def test_calibre_duplicate_is_recorded(lab, monkeypatch):
    lab.write("A/B/B.epub", b"data")
    monkeypatch.setenv("FAKE_MODE", "duplicate")
    assert lab.run().duplicate == 1
    assert lab.run().skipped == 1
    assert len(lab.calls()) == 1


def test_new_format_in_known_dir_adds_only_that_file(lab):
    lab.write("A/B/B.epub", b"epub")
    lab.run()
    lab.write("A/B/B.mobi", b"mobi!")
    assert lab.run().added == 1
    assert sorted(lab.calls()[1]["staged"]) == ["B.mobi"]


def test_removed_files_forgotten_but_empty_scan_keeps_manifest(lab):
    a = lab.write("A/A/A.epub", b"a")
    lab.write("B/B/B.epub", b"bb")
    lab.run()
    a.unlink()
    lab.run()
    files = json.loads((lab.state / "manifest.json").read_text())["files"]
    assert list(files) == ["B/B/B.epub"]
    (lab.incoming / "B/B/B.epub").unlink()  # source looks empty (e.g. unmounted)
    lab.run()
    assert list(json.loads((lab.state / "manifest.json").read_text())["files"]) == ["B/B/B.epub"]


def test_corrupt_manifest_is_moved_aside(lab):
    (lab.state / "manifest.json").write_text("{not json")
    m = imp.Manifest.load(lab.state / "manifest.json")
    assert m.files == {}
    assert any(p.name.startswith("manifest.json.corrupt-") for p in lab.state.iterdir())


def test_forget_cli_forces_reimport(lab, monkeypatch):
    lab.write("A/B/B.epub", b"data")
    lab.run()
    monkeypatch.setenv("INCOMING", str(lab.incoming))
    monkeypatch.setenv("STATE_DIR", str(lab.state))
    assert imp.main(["--forget", f"{lab.incoming}/A/B/B.epub"]) == 0
    assert lab.run().added == 1
    assert len(lab.calls()) == 2


@pytest.mark.parametrize("rc,out,status,ids", [
    (0, "Added book ids: 43, 44", "added", [43, 44]),
    (0, "Merged book ids: 7", "merged", [7]),
    (0, "The following books were not added as they already exist in the database", "duplicate", []),
    (1, "Forbidden", "failed", []),
    (0, "Traceback (most recent call last):", "failed", []),
    (0, "", "failed", []),
])
def test_parse_add_output(rc, out, status, ids):
    r = imp.parse_add_output(rc, out)
    assert (r.status, r.ids) == (status, ids)
