"""Direct Loki push for the one-shot runs (Alloy misses containers that exit in
seconds), so {job="grocery-planner"} exists for every scheduled run."""
import json

import pytest

from grocery import run as R


@pytest.fixture(autouse=True)
def _clear_lines():
    R._LINES.clear()
    yield
    R._LINES.clear()


class _Resp:
    def read(self):
        return b""


def test_labels_follow_the_container_suffix():
    assert R.loki_stream_labels({"GROCERY_SUFFIX": "-poll", "NODE_NAME": "kdk-dkr-01"}) == {
        "job": "grocery-planner", "container": "grocery-planner-poll", "host": "kdk-dkr-01"}
    assert R.loki_stream_labels({})["container"] == "grocery-planner"


def test_body_has_one_increasing_timestamp_per_line():
    body = R.loki_body(["a", "b"], {"job": "grocery-planner"}, 100)
    assert body["streams"][0]["values"] == [["100", "a"], ["101", "b"]]


def test_jlog_lines_are_pushed_once(capsys):
    R.jlog(event="poll", week="2026-W47")
    R.jlog(event="poll_applied", week="2026-W47")
    sent = []

    def opener(req, timeout):
        sent.append((req.full_url, json.loads(req.data)))
        return _Resp()

    env = {"LOKI_URL": "http://loki:3100/", "GROCERY_SUFFIX": "-poll"}
    assert R.flush_to_loki(env, opener=opener) == 2
    url, body = sent[0]
    assert url == "http://loki:3100/loki/api/v1/push"
    stream = body["streams"][0]
    assert stream["stream"]["container"] == "grocery-planner-poll"
    events = [json.loads(v[1])["event"] for v in stream["values"]]
    assert events == ["poll", "poll_applied"]
    assert R.flush_to_loki(env, opener=opener) == 0      # buffer cleared, no re-push
    # stdout is unchanged (docker logs still has the lines)
    assert capsys.readouterr().out.count('"job": "grocery-planner"') == 2


def test_push_failure_never_raises(monkeypatch, capsys):
    monkeypatch.setattr("time.sleep", lambda s: None)
    R.jlog(event="planned")

    def opener(req, timeout):
        raise OSError("connection refused")

    assert R.flush_to_loki({"LOKI_URL": "http://loki:3100"}, opener=opener) == 0
    assert "loki push failed" in capsys.readouterr().err


def test_empty_loki_url_disables_push():
    R.jlog(event="planned")
    assert R.flush_to_loki({"LOKI_URL": ""}, opener=lambda *a, **k: 1 / 0) == 0


def test_crash_is_logged_and_pushed(monkeypatch):
    pushed = []
    monkeypatch.setattr(R, "flush_to_loki", lambda: pushed.append(list(R._LINES)))

    def boom(a):
        raise RuntimeError("tandoor login 403")

    monkeypatch.setattr(R, "_dispatch", boom)
    with pytest.raises(RuntimeError):
        R.main(["--mode", "weekly"])
    assert json.loads(pushed[0][-1])["event"] == "run_error"
