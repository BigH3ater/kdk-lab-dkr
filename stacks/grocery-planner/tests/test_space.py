"""The Tandoor active-space startup check (item 4). Pure helpers, no network."""
import pytest

from grocery.tandoor import resolve_active_space, space_matches, Tandoor


SPACES = [{"id": 3, "name": "Mack House"}]
USER_SPACES = [{"id": 5, "space": 3, "active": True}]


def test_resolve_active_space():
    assert resolve_active_space(SPACES, USER_SPACES) == (3, "Mack House")
    # space given as a nested object also resolves
    assert resolve_active_space(SPACES, [{"space": {"id": 3}, "active": True}]) == (3, "Mack House")
    assert resolve_active_space(SPACES, [{"space": 3, "active": False}]) == (None, None)


def test_space_matches():
    assert space_matches("Mack House", 3, "Mack House", 3) is True
    assert space_matches("Other Space", 9, "Mack House", 3) is False
    assert space_matches("Anything", 3, None, 3) is True     # match by id when no name
    assert space_matches("x", 9, None, 3) is False


def test_verify_space_raises_on_wrong_space(monkeypatch):
    t = Tandoor("https://x", "u", "p", space="Mack House", space_id=3)
    monkeypatch.setattr(t, "get_all", lambda path: {
        "/api/space/": [{"id": 7, "name": "Someone Else"}],
        "/api/user-space/": [{"space": 7, "active": True}]}[path])
    with pytest.raises(RuntimeError):
        t.verify_space()


def test_verify_space_ok(monkeypatch):
    t = Tandoor("https://x", "u", "p", space="Mack House", space_id=3)
    monkeypatch.setattr(t, "get_all", lambda path: {
        "/api/space/": SPACES, "/api/user-space/": USER_SPACES}[path])
    assert t.verify_space() == (3, "Mack House")
