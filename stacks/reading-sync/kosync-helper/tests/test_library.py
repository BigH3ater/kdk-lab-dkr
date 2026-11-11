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
