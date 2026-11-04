import json
import logging
import re
import sys
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─── LOCAL PATCH (libreseerr v0.9.1) ──────────────────────────────────────────
# See stacks/dmz-apps/compose.yaml for the full rationale. Two fixes versus
# upstream v0.9.1:
#   1. Queue format is configurable (queue_type) so the audiobook server marks
#      AudioBook Wanted instead of always eBook.
#   2. search_books()/lookup_by_isbn() rank results best-first so create_request
#      and the add_book fallback (both take results[0]) stop grabbing the box
#      set / bundle that LazyLibrarian+Hardcover tends to return first.
#   3. add_book checks the addBook/queueBook replies (HTTP 200 carries errors
#      too), skips queueBook for a book already owned in that format (queueBook
#      would flip Open -> Wanted and re-download it), and calls searchBook
#      after queueBook so a request searches now, not at the next 6-hourly run.
# Everything else is byte-identical to upstream. Drop this patch once upstream
# fixes these issues.

# Titles that are collections/box sets rather than the single requested book.
_BUNDLE_PATTERNS = [
    re.compile(r"box\s*-?\s*set", re.I),
    re.compile(r"\bbundle\b", re.I),
    re.compile(r"\bomnibus\b", re.I),
    re.compile(r"\bcollection\b", re.I),
    re.compile(r"\banthology\b", re.I),
    re.compile(r"\b\d+\s*-?\s*books?\s+set\b", re.I),   # "7 Books Set", "7-Book Set"
    re.compile(r"\bbooks?\s+set\b", re.I),               # "Books Set"
    re.compile(r"\bbooks?\s+\d+\s*[-–]\s*\d+\b", re.I),  # "Books 1-7"
    re.compile(r"\bvolumes?\s+\w+\s*[-–]\s*(?:volume\s+)?\w+\b", re.I),  # "Volume I - Volume V"
]

_WORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")
_ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+")


def _normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace."""
    t = (text or "").lower()
    t = _WORD_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def _normalize_title(title: str) -> str:
    """Normalize a title: drop a ': subtitle', punctuation, case and leading article."""
    t = (title or "").split(":", 1)[0]
    t = _normalize(t)
    t = _ARTICLE_RE.sub("", t)
    return t


def is_bundle(title: str) -> bool:
    """True when the title looks like a box set / bundle / collection, not one book."""
    t = title or ""
    return any(p.search(t) for p in _BUNDLE_PATTERNS)


def _fuzz(record: dict, *keys: str) -> Optional[float]:
    """First present numeric fuzz field (LazyLibrarian findBook match %), else None."""
    for k in keys:
        v = record.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


def _score_record(record: dict, query_norm: str) -> float:
    """Score one LazyLibrarian findBook/searchItem record against a normalized query.

    Higher is better. Ordering of signals (strongest first):
      1. box set / bundle / collection titles are heavily deprioritised;
      2. an exact-ish normalised title match (+ author match) wins;
      3. then LazyLibrarian's own fuzz fields break remaining ties.
    Nothing is dropped — this only reorders.
    """
    title_norm = _normalize_title(record.get("bookname", ""))
    author_norm = _normalize(record.get("authorname", ""))

    score = 0.0
    if is_bundle(record.get("bookname", "")):
        score -= 1000.0
    # The query from app.py is "<title> <author>"; a real single book has its
    # whole normalised title inside the query, and so does its author.
    if title_norm and title_norm in query_norm:
        score += 100.0
    if author_norm and author_norm in query_norm:
        score += 50.0
    fuzz = _fuzz(record, "highest_fuzz", "book_fuzz", "author_fuzz")
    if fuzz is not None:
        score += max(0.0, min(fuzz, 100.0))
    # Tie-breaker: prefer the shorter (less compound) title.
    score -= len(title_norm) * 0.001
    return score


def rank_books(records: list, query: str) -> list:
    """Return LazyLibrarian records reordered best-first for `query` (stable)."""
    if not isinstance(records, list):
        return records
    query_norm = _normalize_title(query)
    return sorted(records, key=lambda r: _score_record(r, query_norm), reverse=True)


def _reply_ok(result) -> bool:
    """True when a LazyLibrarian addBook/queueBook reply means success.

    The API returns HTTP 200 either way: addBook answers JSON true/false,
    queueBook the text "OK" or "Invalid id: <id>", and API errors a dict with
    "Success": false."""
    if result is True:
        return True
    if result is False or result is None:
        return False
    if isinstance(result, dict):
        return result.get("Success") is not False and not result.get("Error")
    if isinstance(result, str):
        t = result.strip().lower()
        return t in ("ok", "true") or t.startswith("ok")
    return bool(result)
# ─── end LOCAL PATCH ──────────────────────────────────────────────────────────


class LazyLibrarianClient:
    """Client for interacting with a LazyLibrarian instance."""

    def __init__(self, base_url: str, api_key: str, queue_type: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        # LOCAL PATCH: queueBook type ("eBook" or "AudioBook"); see module header.
        # Upstream app.get_client(server_type) builds one client per type with
        # the same URL and key, so the type isn't passed in. Rather than vendor
        # all of app.py to change one line, read `server_type` from the caller's
        # frame (get_client's local). An explicit queue_type wins; anything
        # unexpected falls back to upstream's eBook.
        if queue_type is None:
            queue_type = "eBook"
            try:
                caller = sys._getframe(1).f_locals
                if caller.get("server_type") == "audiobook":
                    queue_type = "AudioBook"
            except Exception:  # noqa: BLE001
                pass
        self.queue_type = queue_type
        self.session = requests.Session()

    def _get(self, cmd: str, **params) -> dict | list | str:
        """Make a GET request to the LazyLibrarian API."""
        query = {"apikey": self.api_key, "cmd": cmd}
        query.update(params)
        resp = self.session.get(f"{self.base_url}/api", params=query, timeout=15)
        resp.raise_for_status()
        try:
            return resp.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            return resp.text.strip()

    def test_connection(self) -> dict:
        """Test connection to the LazyLibrarian instance."""
        result = self._get("getVersion")
        if isinstance(result, str):
            return {"version": result}
        if isinstance(result, list) and result:
            return result[0] if isinstance(result[0], dict) else {"version": str(result[0])}
        if isinstance(result, dict):
            return result
        return {"version": "unknown"}

    def search_books(self, query: str) -> list:
        """Search for books by name using findBook."""
        result = self._get("findBook", name=query)
        if not isinstance(result, list):
            return []
        result = rank_books(result, query)  # LOCAL PATCH: best-first, drop bundles
        return [
            {
                "title": b.get("bookname", "Unknown"),
                "author": {
                    "authorName": b.get("authorname", "Unknown"),
                    "foreignAuthorId": b.get("authorid", ""),
                },
                "foreignBookId": b.get("bookid", ""),
                "foreignEditionId": b.get("bookisbn", ""),
                "overview": b.get("bookdesc", ""),
                "releaseDate": b.get("bookdate", ""),
                "ratings": {"value": float(b.get("bookrate", 0))} if b.get("bookrate") else {},
            }
            for b in result
        ]

    def lookup_by_isbn(self, isbn: str) -> list:
        """Look up a book by ISBN using searchItem."""
        result = self._get("searchItem", item=isbn)
        if not isinstance(result, list):
            return []
        result = rank_books(result, isbn)  # LOCAL PATCH: best-first, drop bundles
        return [
            {
                "title": b.get("bookname", "Unknown"),
                "author": {
                    "authorName": b.get("authorname", "Unknown"),
                    "foreignAuthorId": b.get("authorid", ""),
                },
                "foreignBookId": b.get("bookid", ""),
                "foreignEditionId": b.get("bookisbn", ""),
                "overview": b.get("bookdesc", ""),
                "releaseDate": b.get("bookdate", ""),
            }
            for b in result
        ]

    def lookup_author(self, name: str) -> list:
        """Look up an author by name using findAuthor."""
        result = self._get("findAuthor", name=name)
        if not isinstance(result, list):
            return []
        return [
            {
                "authorName": a.get("authorname", "Unknown"),
                "foreignAuthorId": a.get("authorid", ""),
            }
            for a in result
        ]

    def get_quality_profiles(self) -> list:
        """Return a synthetic quality profile since LazyLibrarian doesn't have them."""
        return [{"id": 1, "name": "Default"}]

    def get_root_folders(self) -> list:
        """Return a synthetic root folder since LazyLibrarian manages its own paths."""
        return [{"path": "/books"}]

    def add_book(self, book_data: dict, quality_profile_id: int, root_folder: str) -> dict:
        """Add a book to LazyLibrarian and mark it as wanted."""
        book_id = book_data.get("foreignBookId", "")
        title = book_data.get("title", "Unknown")

        if not book_id:
            # Try to find the book first
            results = self.search_books(f"{title} {book_data.get('author', {}).get('authorName', '')}")
            if results:
                book_id = results[0].get("foreignBookId", "")

        if not book_id:
            raise ValueError(f"Could not find book ID for '{title}' in LazyLibrarian")

        # Add the book to the database
        logger.info("Adding book to LazyLibrarian: '%s' (id=%s)", title, book_id)
        # LOCAL PATCH: addBook runs in a background thread and returns "OK"
        # immediately, so an immediate queueBook answers "Invalid id: <id>"
        # and the request is silently never queued (seen 2026-11-03 on 31 of 31
        # books). wait=1 makes LazyLibrarian finish the add first.
        add_result = self._get("addBook", id=book_id, wait=1)
        logger.info("addBook result: %s", add_result)
        # LOCAL PATCH: LazyLibrarian answers HTTP 200 for failures too (addBook
        # -> false, queueBook -> "Invalid id: <id>"), so check the replies
        # instead of reporting every request as queued.
        if not _reply_ok(add_result):
            raise ValueError(f"LazyLibrarian addBook failed for '{title}' (id={book_id}): {add_result}")

        # LOCAL PATCH: never set Wanted on a book LazyLibrarian already has in
        # this format -- queueBook flips an Open (owned) book to Wanted and the
        # next search downloads it again.
        if self._already_have(book_id):
            logger.info("'%s' (id=%s) is already in the %s library; not queueing",
                        title, book_id, self.queue_type)
            return {"id": book_id, "title": title, "foreignBookId": book_id,
                    "already_available": True}

        # Mark the book as wanted to trigger a search
        # LOCAL PATCH: honour self.queue_type (AudioBook vs eBook); see module header.
        queue_result = self._get("queueBook", id=book_id, type=self.queue_type)
        logger.info("queueBook result: %s", queue_result)
        if not _reply_ok(queue_result):
            raise ValueError(f"LazyLibrarian queueBook failed for '{title}' (id={book_id}): {queue_result}")

        # LOCAL PATCH: queueBook only sets the status; nothing searches until the
        # 6-hourly scheduled Book search. searchBook (no wait: it runs in the
        # background and answers OK at once) makes the request search now. A
        # failure here is logged, not raised: the book is Wanted either way.
        try:
            search_result = self._get("searchBook", id=book_id, type=self.queue_type)
            logger.info("searchBook result: %s", search_result)
        except Exception as e:  # noqa: BLE001
            logger.warning("searchBook for '%s' (id=%s) failed: %s", title, book_id, e)

        return {
            "id": book_id,
            "title": title,
            "foreignBookId": book_id,
        }

    def _already_have(self, book_id: str) -> bool:
        """LOCAL PATCH: True if LazyLibrarian already owns this book in
        self.queue_type's format (status Open or Have). Any lookup error counts
        as 'not owned' so a request is never silently dropped."""
        field = "audiostatus" if self.queue_type == "AudioBook" else "status"
        for status in ("Open", "Have"):
            try:
                rows = self._get("getAllBooks", **{field: status})
            except Exception as e:  # noqa: BLE001
                logger.warning("getAllBooks %s=%s failed: %s", field, status, e)
                return False
            if isinstance(rows, list) and any(
                str(r.get("BookID", r.get("bookid", ""))) == str(book_id)
                for r in rows if isinstance(r, dict)
            ):
                return True
        return False

    def get_queue(self) -> list:
        """Get wanted books (equivalent to a download queue)."""
        result = self._get("getWanted")
        if not isinstance(result, list):
            return []
        return [
            {
                "title": b.get("bookname", "Unknown"),
                "status": "downloading",
                "size": 0,
                "sizeleft": 0,
                "bookId": b.get("bookid", ""),
            }
            for b in result
        ]

    def get_book_status(self, book_id: int) -> Optional[dict]:
        """Get the status of a specific book."""
        # LazyLibrarian doesn't have a direct "get book by ID" for status,
        # so we check snatched books
        result = self._get("getSnatched")
        if isinstance(result, list):
            for b in result:
                if str(b.get("bookid", "")) == str(book_id):
                    return {
                        "id": book_id,
                        "title": b.get("bookname", "Unknown"),
                        "statistics": {"bookFileCount": 1},
                    }
        return None

    def get_books(self) -> list:
        """Get all books from the LazyLibrarian library."""
        result = self._get("getBooks")
        if not isinstance(result, list):
            return []
        return result

    def get_history(self) -> list:
        """Get snatched/download history."""
        result = self._get("getSnatched")
        if not isinstance(result, list):
            return []
        return [
            {
                "title": b.get("bookname", "Unknown"),
                "status": "completed",
                "date": b.get("added", ""),
            }
            for b in result
        ]
