"""End-to-end checks through the FastAPI app with a mocked subprocess:

- the pre-encode sweep only ever runs while the single-encode lock is held;
- a failed encode cleans up the job folder DeeZy left behind.
"""
import os

import pytest
from fastapi.testclient import TestClient

import main

# Dummy bearer value for the auth header -- not a real credential.
FAKE_AUTH = "dummy-" + "bearer-" + "value"


class FakePopen:
    """Stand-in for subprocess.Popen so no real DeeZy runs. Behaviour is driven
    by the class-level `behavior` dict the fixtures set per test."""

    behavior = {
        "returncode": 0,
        "stderr": "",
        "make_output": True,
        "leftover": None,   # name of a job folder to leave behind
        "work_dir": None,
        "lock_held_at_run": None,  # recorded: was the encode lock held?
    }

    def __init__(self, cmd, stdout=None, stderr=None, text=None):
        self.cmd = cmd
        self.pid = 1234
        self.returncode = FakePopen.behavior["returncode"]
        self._out = cmd[cmd.index("--output") + 1]

    def communicate(self, timeout=None):
        b = FakePopen.behavior
        b["lock_held_at_run"] = main._ENCODE_LOCK.locked()
        if b["make_output"]:
            open(self._out, "wb").close()
        if b["leftover"]:
            p = os.path.join(b["work_dir"], b["leftover"])
            os.makedirs(p, exist_ok=True)
            with open(os.path.join(p, "intermediate.wav"), "wb") as fh:
                fh.write(b"x" * 32)
        return (None, b["stderr"])


@pytest.fixture
def env(tmp_path, monkeypatch):
    exchange = tmp_path / "exchange"
    (exchange / "inbound").mkdir(parents=True)
    (exchange / "outbound").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    logs = tmp_path / "logs"
    # input file the encode resolves under inbound/
    (exchange / "inbound" / "Lossless.Title.mkv").write_bytes(b"fake")

    monkeypatch.setenv("DEE_WRAPPER_TOKEN", FAKE_AUTH)
    monkeypatch.setenv("DEE_EXCHANGE_ROOT", str(exchange))
    monkeypatch.setenv("DEE_WRAPPER_MIN_FREE_GB", "0")  # don't fail pre-flight
    monkeypatch.setenv("DEE_WRAPPER_WORK_DIR", str(work))
    monkeypatch.setenv("DEE_WRAPPER_LOG_DIR", str(logs))
    monkeypatch.setattr(main.subprocess, "Popen", FakePopen)

    FakePopen.behavior.update(
        returncode=0, stderr="", make_output=True,
        leftover=None, work_dir=str(work), lock_held_at_run=None,
    )
    return {"work": str(work)}


def _post(client):
    return client.post(
        "/encode",
        json={"filename": "Lossless.Title.mkv", "format": "ddp"},
        headers={"Authorization": f"Bearer {FAKE_AUTH}"},
    )


def test_pre_encode_sweep_runs_under_the_lock(env, monkeypatch):
    real_sweep = main._sweep_work_dir
    seen = []

    def spy(work_dir, max_age_hours, **kw):
        seen.append(main._ENCODE_LOCK.locked())
        return real_sweep(work_dir, max_age_hours, **kw)

    monkeypatch.setattr(main, "_sweep_work_dir", spy)

    app = main.create_app()
    with TestClient(app) as client:   # __enter__ runs the startup (lifespan) sweep
        seen.clear()                  # drop the startup-sweep record
        r = _post(client)

    assert r.status_code == 200, r.text
    # Exactly one sweep during /encode, and the lock was held for it.
    assert seen == [True]
    # The mocked DeeZy also observed the lock held while "encoding".
    assert FakePopen.behavior["lock_held_at_run"] is True
    assert main._ENCODE_LOCK.locked() is False   # released afterwards


def test_failed_encode_cleans_up_leftover_folder(env):
    FakePopen.behavior.update(
        returncode=1, stderr="boom: DEE licence error\n",
        make_output=False, leftover="Lossless_Title__2024_abcd1234_t0",
    )
    app = main.create_app()
    with TestClient(app) as client:
        r = _post(client)

    assert r.status_code == 500
    leftover = os.path.join(env["work"], "Lossless_Title__2024_abcd1234_t0")
    assert not os.path.exists(leftover)       # the failed job's folder was removed
    assert main._ENCODE_LOCK.locked() is False


def test_successful_encode_returns_output_name(env):
    app = main.create_app()
    with TestClient(app) as client:
        r = _post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["output_filename"] == "Lossless.Title.ec3"


def test_healthz_reports_temp_dirs(env):
    # Drop a leftover job folder, then confirm /healthz surfaces it.
    work = env["work"]
    os.makedirs(os.path.join(work, "Stale_00ff00ff_t0"))
    with open(os.path.join(work, "Stale_00ff00ff_t0", "a.wav"), "wb") as fh:
        fh.write(b"z" * 1024)
    app = main.create_app()
    with TestClient(app) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["temp_dirs"] == 1
    assert body["temp_gb"] >= 0
