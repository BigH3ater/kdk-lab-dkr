"""KOReader hash -> ABS ebook item map (primary PDFs and primary EPUBs).

PDFs are served by the helper itself (page sync). EPUBs stay BookBridge's; the EPUB map
only tells the helper which ABS item a tablet hash is, so it can create the missing
BookBridge ebook-only link (epub_link.py).

The helper mounts the ebook library read-only at the SAME container path ABS uses
(/mnt/media/books/ebook -> /ebooks:ro), so the path ABS reports for an item's ebook
file is opened directly. Hashing needs only twelve 1 KiB reads per file, and reading
the mount (not /api/items/:id/ebook) keeps the hourly refresh off ABS's download
counters (AbsMassDownload alert).
"""
from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass

from .partial_md5 import partial_md5_fileobj

log = logging.getLogger("kosync_helper")


@dataclass(frozen=True)
class PdfEntry:
    item_id: str
    pages: int | None


@dataclass(frozen=True)
class EpubEntry:
    item_id: str
    abs_path: str   # the path ABS reports for the item's primary ebook
    real_path: str  # the same file, resolved inside the mount
    size: int


def safe_path(root: str, path: str) -> str | None:
    """The real path of `path` if it is a regular file inside `root`, else None."""
    if not isinstance(path, str) or "\x00" in path:
        return None
    real_root = os.path.realpath(root)
    real = os.path.realpath(path)
    if not real.startswith(real_root.rstrip(os.sep) + os.sep):
        return None
    try:
        st = os.stat(real)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return real


def count_pages(f) -> int | None:
    try:
        from pypdf import PdfReader

        f.seek(0)
        n = len(PdfReader(f, strict=False).pages)
        return n if n > 0 else None
    except Exception:  # a PDF pypdf cannot parse: fall back to KOReader's percentage
        return None


class PdfLibrary:
    def __init__(self, root: str):
        self.root = root
        self._by_hash: dict[str, PdfEntry] = {}
        self._epub_by_hash: dict[str, EpubEntry] = {}
        # (real path, size, mtime_ns) -> (hash, pages): unchanged files are not re-read
        self._file_cache: dict[tuple[str, int, int], tuple[str, int | None]] = {}

    def __len__(self) -> int:
        """The PDF count (the EPUB count is epub_count)."""
        return len(self._by_hash)

    @property
    def epub_count(self) -> int:
        return len(self._epub_by_hash)

    def lookup(self, doc_hash: str) -> PdfEntry | None:
        """PDFs only: the helper answers these itself."""
        return self._by_hash.get(doc_hash.lower())

    def lookup_epub(self, doc_hash: str) -> EpubEntry | None:
        return self._epub_by_hash.get(doc_hash.lower())

    def rebuild(self, items: list[tuple]) -> int:
        """Replace the maps from (item_id, abs_path[, format]) tuples; the format
        defaults to pdf. Returns the PDF count."""
        by_hash: dict[str, PdfEntry] = {}
        epubs: dict[str, EpubEntry] = {}
        cache: dict[tuple[str, int, int], tuple[str, int | None]] = {}
        for item in items:
            item_id, abs_path = item[0], item[1]
            fmt = item[2] if len(item) > 2 else "pdf"
            if fmt not in ("pdf", "epub"):
                continue
            real = safe_path(self.root, abs_path)
            if real is None:
                log.warning("pdf_map_skip", extra={"event": "pdf_map_skip", "item": item_id})
                continue
            try:  # a file removed mid-rebuild skips its entry, never the whole rebuild
                st = os.stat(real)
                key = (real, st.st_size, st.st_mtime_ns)
                cached = self._file_cache.get(key)
                if cached is None:
                    with open(real, "rb") as f:
                        cached = (partial_md5_fileobj(f), count_pages(f) if fmt == "pdf" else None)
            except OSError:
                log.warning("pdf_map_skip", extra={"event": "pdf_map_skip", "item": item_id})
                continue
            cache[key] = cached
            doc_hash, pages = cached
            if fmt == "pdf":
                by_hash[doc_hash] = PdfEntry(item_id=item_id, pages=pages)
            else:
                epubs[doc_hash] = EpubEntry(item_id=item_id, abs_path=abs_path, real_path=real, size=st.st_size)
        for doc_hash in by_hash:  # a hash on both sides stays a PDF and is never auto-linked
            epubs.pop(doc_hash, None)
        self._by_hash = by_hash
        self._epub_by_hash = epubs
        self._file_cache = cache
        return len(by_hash)
