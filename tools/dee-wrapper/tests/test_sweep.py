"""Sweep of stale DeeZy job folders (the 2026-11-03 temp-folder incident)."""
import os
import time

import main


def _mkjob(root, name, age_hours, *, files=(("intermediate.wav", 1024),)):
    """Create a job folder `name` under `root` whose whole tree is `age_hours`
    old, with some files. Returns its path."""
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    total = 0
    for fname, size in files:
        with open(os.path.join(path, fname), "wb") as fh:
            fh.write(b"x" * size)
            total += size
    old = time.time() - age_hours * 3600
    # set every entry's mtime (files first, then the dir) to `old`
    for fname, _ in files:
        os.utime(os.path.join(path, fname), (old, old))
    os.utime(path, (old, old))
    return path, total


def test_job_dir_pattern_matches_and_rejects():
    good = [
        "ReturnoftheKing_1a2b3c4d_t0",
        "ReturnoftheKing_1a2b3c4d_t0_9f3a",
        "Movie2024_deadBEEF_t0",
        # Real DeeZy folder names seen on kdk-dee-01, 2026-11-03: DeeZy uses the
        # first <=20 chars of the input filename, so underscores/hyphens occur.
        "The_Lord_of_the_Ring_4bf1f260_t0_beab",
        "2010__1984___Remux-1_15a3f8c6_t0_6c7a",
        "Bluey__2018__-_S01E2_6f432da1_t0",
        "Dr__No__1962___Remux_96079db8_t0_3119",
        "Vikings__2013__-_S06_213576d7_t0",
    ]
    bad = [
        "logs",
        ".venv",
        "__pycache__",
        "random_folder",
        "Movie_12345678_t1",            # wrong track marker
        "Movie_12345678_t0_zzzz",       # non-hex suffix
        "Movie_1234_t0",                # 8-hex id required
        "inbound",
        "A_Name_Longer_Than_Twenty_Chars_1a2b3c4d_t0",  # DeeZy caps the stem at 20
    ]
    for name in good:
        assert main.JOB_DIR_RE.match(name), name
    for name in bad:
        assert not main.JOB_DIR_RE.match(name), name


def test_sweep_removes_old_keeps_fresh(tmp_path):
    work = str(tmp_path)
    old_path, old_sz = _mkjob(work, "OldMovie_11111111_t0", age_hours=10)
    fresh_path, _ = _mkjob(work, "FreshMovie_22222222_t0", age_hours=1)

    removed, freed = main._sweep_work_dir(work, max_age_hours=6)

    assert removed == ["OldMovie_11111111_t0"]
    assert freed == old_sz
    assert not os.path.exists(old_path)
    assert os.path.exists(fresh_path)


def test_sweep_never_touches_non_matching_entries(tmp_path):
    work = str(tmp_path)
    # Old, but NOT a DeeZy job folder -> must be left alone.
    other = os.path.join(work, "important_stuff")
    os.makedirs(other)
    old = time.time() - 100 * 3600
    os.utime(other, (old, old))
    # A loose old file directly in the work dir -> left alone (not a dir).
    loose = os.path.join(work, "notes.txt")
    with open(loose, "w") as fh:
        fh.write("keep me")
    os.utime(loose, (old, old))
    # A genuinely old job folder -> removed.
    _mkjob(work, "Gone_abcdef01_t0", age_hours=99)

    removed, _ = main._sweep_work_dir(work, max_age_hours=6)

    assert removed == ["Gone_abcdef01_t0"]
    assert os.path.isdir(other)
    assert os.path.isfile(loose)


def test_sweep_keeps_folder_with_fresh_inner_file(tmp_path):
    """Top-dir mtime is old but a file inside was just written (an in-flight
    encode): the folder must be kept, since _newest_mtime sees the fresh file."""
    work = str(tmp_path)
    path = os.path.join(work, "Live_abcd1234_t0")
    os.makedirs(path)
    old = time.time() - 50 * 3600
    inner = os.path.join(path, "fresh.wav")
    with open(inner, "wb") as fh:
        fh.write(b"y" * 10)
    # Make the directory itself look old, but leave the inner file at "now".
    os.utime(path, (old, old))

    removed, _ = main._sweep_work_dir(work, max_age_hours=6)

    assert removed == []
    assert os.path.isdir(path)


def test_sweep_empty_and_missing_dir(tmp_path):
    assert main._sweep_work_dir(str(tmp_path), 6) == ([], 0)
    missing = os.path.join(str(tmp_path), "does-not-exist")
    assert main._sweep_work_dir(missing, 6) == ([], 0)


def test_temp_usage_counts_only_job_folders(tmp_path):
    work = str(tmp_path)
    _mkjob(work, "A_00000001_t0", age_hours=1, files=(("a.wav", 2048),))
    _mkjob(work, "B_00000002_t0", age_hours=1, files=(("b.wav", 4096),))
    os.makedirs(os.path.join(work, "not_a_job"))
    count, gb = main._temp_usage(work)
    assert count == 2
    assert gb == (2048 + 4096) / (1024 ** 3)
