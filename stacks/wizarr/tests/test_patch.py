"""stacks/wizarr: the LDAP patch applies to upstream and the pins agree.

fixtures/client_v2026.10.1.py is upstream's /app/app/services/ldap/client.py,
byte for byte from ghcr.io/wizarrrr/wizarr:v2026.10.1 (sha256 below, read from
the running container on kdk-dkr-dmz-01, 2026-11-07). Upstream licence: MIT.
"""
import ast
import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parents[1]
REPO = STACK.parents[1]
FIXTURE = STACK / "tests/fixtures/client_v2026.10.1.py"
FIXTURE_SHA256 = "488a303b7b4234080df7f9883776bd3ed3de10874436f9b85f6bb71921e5ace4"
PATCH = STACK / "patches/0001-refuse-existing-ldap-user.patch"
TARGET = "app/services/ldap/client.py"

spec = importlib.util.spec_from_file_location("kdk_apply", STACK / "patches/apply.py")
apply_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply_mod)


def _root_with(tmp_path, text):
    (tmp_path / TARGET).parent.mkdir(parents=True)
    (tmp_path / TARGET).write_text(text)
    return tmp_path


def _func(tree, cls, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"{cls}.{name} not found")


def _calls(fn):
    return {n.func.attr for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}


def test_fixture_is_the_upstream_file():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


def test_upstream_create_user_updates_existing_entries():
    # The finding the patch fixes (wizarr-public-readiness-2026-11-07 F1).
    fn = _func(ast.parse(FIXTURE.read_text()), "LDAPClient", "create_user")
    assert "_update_existing_user" in _calls(fn)


def test_patch_refuses_existing_user(tmp_path):
    root = _root_with(tmp_path, FIXTURE.read_text())
    apply_mod.apply(PATCH.read_text(), root)
    src = (root / TARGET).read_text()
    tree = ast.parse(src)
    fn = _func(tree, "LDAPClient", "create_user")
    assert "_update_existing_user" not in _calls(fn)
    assert "warning" in _calls(fn)
    body = ast.get_source_segment(src, fn)
    assert 'return False, "That username is already taken"' in body
    # Minimal patch: the dead helper and the create path are untouched.
    _func(tree, "LDAPClient", "_update_existing_user")
    assert "_create_new_user" in _calls(fn)


def test_patch_fails_on_changed_upstream(tmp_path):
    changed = FIXTURE.read_text().replace(
        "return self._update_existing_user(conn, user_dn, email, password)",
        "return self._update_existing_user(conn, user_dn, email, password, extra)")
    root = _root_with(tmp_path, changed)
    with pytest.raises(SystemExit, match="exactly once"):
        apply_mod.apply(PATCH.read_text(), root)
    assert (root / TARGET).read_text() == changed  # nothing half-written


def test_patch_fails_when_already_applied(tmp_path):
    root = _root_with(tmp_path, FIXTURE.read_text())
    apply_mod.apply(PATCH.read_text(), root)
    with pytest.raises(SystemExit):
        apply_mod.apply(PATCH.read_text(), root)


def _from_ref():
    refs = re.findall(r"^FROM\s+(\S+)", (STACK / "Dockerfile").read_text(), re.M)
    assert len(refs) == 1, refs
    return refs[0]


def test_dockerfile_from_is_digest_pinned_upstream():
    assert re.fullmatch(r"ghcr\.io/wizarrrr/wizarr:v[\d.]+@sha256:[0-9a-f]{64}", _from_ref())


def test_local_tag_and_docs_match_the_from_pin():
    # A Renovate FROM bump turns this red until the local tag and the docs row
    # move with it: the cue to re-check the patch against the new upstream.
    ref = _from_ref()
    version = ref.split(":", 1)[1].split("@")[0]
    compose = (STACK / "compose.yaml").read_text()
    assert re.search(rf"^\s+image: kdk/wizarr:{re.escape(version)}-kdk\d+$", compose, re.M)
    assert "build:" in compose
    docs = (REPO / "docs/services/wizarr.md").read_text()
    assert f"`{ref}`" in docs


def test_build_context_ships_the_patches():
    ignore = (STACK / ".dockerignore").read_text().split()
    assert {"*", "!Dockerfile", "!patches/"} <= set(ignore)
