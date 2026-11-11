"""KOReader hash -> ABS PDF item map.

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
        # (real path, size, mtime_ns) -> (hash, pages): unchanged files are not re-read
        self._file_cache: dict[tuple[str, int, int], tuple[str, int | None]] = {}

    def __len__(self) -> int:
        return len(self._by_hash)

    def lookup(self, doc_hash: str) -> PdfEntry | None:
        return self._by_hash.get(doc_hash.lower())

    def rebuild(self, items: list[tuple[str, str]]) -> int:
        """Replace the map from (item_id, abs_path) pairs. Returns the PDF count."""
        by_hash: dict[str, PdfEntry] = {}
        cache: dict[tuple[str, int, int], tuple[str, int | None]] = {}
        for item_id, abs_path in items:
            real = safe_path(self.root, abs_path)
            if real is None:
                log.warning("pdf_map_skip", extra={"event": "pdf_map_skip", "item": item_id})
                continue
            st = os.stat(real)
            key = (real, st.st_size, st.st_mtime_ns)
            cached = self._file_cache.get(key)
            if cached is None:
                try:
                    with open(real, "rb") as f:
                        cached = (partial_md5_fileobj(f), count_pages(f))
                except OSError:
                    log.warning("pdf_map_skip", extra={"event": "pdf_map_skip", "item": item_id})
                    continue
            cache[key] = cached
            doc_hash, pages = cached
            by_hash[doc_hash] = PdfEntry(item_id=item_id, pages=pages)
        self._by_hash = by_hash
        self._file_cache = cache
        return len(by_hash)
