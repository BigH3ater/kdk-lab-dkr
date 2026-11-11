import pytest

from conftest import make_pdf
from kosync_helper.config import Settings
from kosync_helper.library import PdfLibrary, safe_path
from kosync_helper.partial_md5 import partial_md5

A = "11111111-2222-4333-8444-555555555555"
B = "22222222-2222-4333-8444-555555555555"


def test_rebuild_maps_hash_to_item_and_pages(tmp_path):
    make_pdf(str(tmp_path / "a.pdf"), 7)
    lib = PdfLibrary(str(tmp_path))
    assert lib.rebuild([(A, str(tmp_path / "a.pdf"))]) == 1
    entry = lib.lookup(partial_md5(str(tmp_path / "a.pdf")))
    assert entry.item_id == A and entry.pages == 7


def test_lookup_is_case_insensitive(tmp_path):
    make_pdf(str(tmp_path / "a.pdf"), 2)
    lib = PdfLibrary(str(tmp_path))
    lib.rebuild([(A, str(tmp_path / "a.pdf"))])
    assert lib.lookup(partial_md5(str(tmp_path / "a.pdf")).upper()).item_id == A


def test_paths_outside_the_library_are_refused(tmp_path):
    root = tmp_path / "ebooks"
    root.mkdir()
    outside = tmp_path / "secret.pdf"
    make_pdf(str(outside), 1)
    (root / "link.pdf").symlink_to(outside)
    lib = PdfLibrary(str(root))
    assert lib.rebuild([(A, str(outside)), (B, str(root / "link.pdf")),
                        (A, str(root / ".." / "secret.pdf")), (B, str(root))]) == 0
    assert safe_path(str(root), "/etc/passwd") is None
    assert safe_path(str(root), "bad\x00path") is None


def test_missing_file_is_skipped(tmp_path):
    lib = PdfLibrary(str(tmp_path))
    assert lib.rebuild([(A, str(tmp_path / "gone.pdf"))]) == 0


def test_file_vanishing_mid_rebuild_skips_only_that_entry(tmp_path, monkeypatch):
    """Review 2026-11-11 #5: a file removed between the path check and its stat skips
    that entry; the rest of the map is still built."""
    import kosync_helper.library as mod

    make_pdf(str(tmp_path / "a.pdf"), 3)
    monkeypatch.setattr(mod, "safe_path", lambda root, p: p)   # the check passed, then the file went
    lib = PdfLibrary(str(tmp_path))
    assert lib.rebuild([(B, str(tmp_path / "gone.pdf")), (A, str(tmp_path / "a.pdf"))]) == 1
    assert lib.lookup(partial_md5(str(tmp_path / "a.pdf"))).item_id == A


def test_unparseable_pdf_has_unknown_pages(tmp_path):
    p = tmp_path / "broken.pdf"
    p.write_bytes(b"%PDF-1.4 garbage" * 200)
    lib = PdfLibrary(str(tmp_path))
    lib.rebuild([(A, str(p))])
    assert lib.lookup(partial_md5(str(p))).pages is None


def test_unchanged_files_are_not_reread(tmp_path, monkeypatch):
    make_pdf(str(tmp_path / "a.pdf"), 3)
    lib = PdfLibrary(str(tmp_path))
    lib.rebuild([(A, str(tmp_path / "a.pdf"))])
    import kosync_helper.library as mod

    def boom(f):
        raise AssertionError("re-read an unchanged file")

    monkeypatch.setattr(mod, "partial_md5_fileobj", boom)
    assert lib.rebuild([(A, str(tmp_path / "a.pdf"))]) == 1


def test_removed_items_drop_out(tmp_path):
    make_pdf(str(tmp_path / "a.pdf"), 3)
    lib = PdfLibrary(str(tmp_path))
    lib.rebuild([(A, str(tmp_path / "a.pdf"))])
    lib.rebuild([])
    assert len(lib) == 0


def test_only_primary_pdf_items_are_listed(client):
    # conftest's ABS library has one PDF item and one EPUB item; only the PDF is mapped
    assert len(client.app.state.helper.library) == 1


@pytest.mark.parametrize("bad", [{"abs_api_key": ""}, {"abs_library_id": "../x"}, {"kosync_user": ""},
                                 {"abs_url": "file:///x"}])
def test_settings_reject_bad_values(bad):
    base = dict(abs_url="http://a", abs_api_key="k", abs_library_id=A, bookbridge_url="http://b", kosync_user="j")
    with pytest.raises(ValueError):
        Settings(**{**base, **bad})


def _item_gets(abs_):
    return [c.url.path.rsplit("/", 1)[1] for c in abs_.calls if "/api/items/" in c.url.path]


def test_item_details_are_cached_by_updated_at(client, fakes):
    """Review 2026-11-11 #4: the hourly refresh re-reads an item only when ABS reports
    it changed (updatedAt in the minified list) or it is new."""
    from conftest import EPUB_ITEM

    _, abs_ = fakes
    helper = client.app.state.helper
    n = len(_item_gets(abs_))
    assert n == 2                                      # the first refresh reads both
    client.portal.call(helper.refresh)
    assert len(_item_gets(abs_)) == n                  # nothing changed: no per-item GET
    abs_.updated[EPUB_ITEM] = 2
    client.portal.call(helper.refresh)
    assert _item_gets(abs_)[n:] == [EPUB_ITEM]
    assert len(helper.library) == 1 and helper.library.epub_count == 1


def test_failed_item_fetch_keeps_the_last_good_map(client, fakes):
    from conftest import PDF_ITEM

    _, abs_ = fakes
    helper = client.app.state.helper
    abs_.updated[PDF_ITEM] = 2
    abs_.item_status = 500
    client.portal.call(helper.refresh)
    assert helper.last_refresh_error and len(helper.library) == 1 and helper.library.epub_count == 1
    abs_.item_status = None
    n = len(_item_gets(abs_))
    client.portal.call(helper.refresh)
    assert not helper.last_refresh_error and _item_gets(abs_)[n:] == [PDF_ITEM]
    assert len(helper.library) == 1
