"""Post-failure cleanup: remove the folder a failed/killed encode left behind."""
import os
import time

import main


def _mkjob(root, name, mtime):
    path = os.path.join(root, name)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "intermediate.wav"), "wb") as fh:
        fh.write(b"x" * 16)
    os.utime(os.path.join(path, "intermediate.wav"), (mtime, mtime))
    os.utime(path, (mtime, mtime))
    return path


def test_cleanup_selects_newest_matching_prefix(tmp_path):
    work = str(tmp_path)
    started = time.time()
    # Two folders for the same safe name; the newer (later mtime) is this run's.
    older = _mkjob(work, "Movie2024_aaaaaaaa_t0", mtime=started + 1)
    newer = _mkjob(work, "Movie2024_bbbbbbbb_t0", mtime=started + 5)

    removed = main._cleanup_failed_job(work, "Movie2024", started)

    assert removed == "Movie2024_bbbbbbbb_t0"
    assert not os.path.exists(newer)
    assert os.path.exists(older)  # only the newest matching folder is removed


def test_cleanup_matches_real_deezy_names_regardless_of_safe_prefix(tmp_path):
    """DeeZy names the folder from the INPUT filename (underscores, hyphens),
    not from _deezy_safe_name, so selection must not depend on the prefix.
    Only one encode runs at a time (lock), so the newest folder active since
    this encode started is this encode's. Regression for 2026-11-03."""
    work = str(tmp_path)
    started = time.time()
    older = _mkjob(work, "Dr__No__1962___Remux_96079db8_t0_3119", mtime=started - 3600)
    this_run = _mkjob(work, "The_Lord_of_the_Ring_4bf1f260_t0_beab", mtime=started + 4)

    removed = main._cleanup_failed_job(work, "TheLordoftheRingsTheReturnoftheKing", started)

    assert removed == "The_Lord_of_the_Ring_4bf1f260_t0_beab"
    assert not os.path.exists(this_run)
    assert os.path.exists(older)  # predates this encode -> left to the sweep


def test_cleanup_ignores_folders_predating_start(tmp_path):
    work = str(tmp_path)
    started = time.time()
    # Matches the prefix but is older than this encode -> leave to the sweep.
    stale = _mkjob(work, "Movie2024_eeeeeeee_t0", mtime=started - 3600)

    removed = main._cleanup_failed_job(work, "Movie2024", started)

    assert removed is None
    assert os.path.exists(stale)


def test_cleanup_prefix_matches_deezy_safe_name():
    """The prefix the handler passes is the stem of _deezy_safe_name(), which
    is exactly what DeeZy names its job folder after."""
    deezy_name = main._deezy_safe_name(main._output_filename("Return.of.the.King.mkv"))
    prefix = deezy_name.rsplit(".", 1)[0]
    assert prefix == "ReturnoftheKing"
    assert main.JOB_DIR_RE.match(prefix + "_1a2b3c4d_t0")
    # ...and the real input-derived names match too.
    assert main.JOB_DIR_RE.match("The_Lord_of_the_Ring_4bf1f260_t0_beab")
