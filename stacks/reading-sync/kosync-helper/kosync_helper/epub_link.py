"""Create the BookBridge ebook-only link for an EPUB the tablet reports.

BookBridge 7.12.0 never auto-links an EPUB while ABS is configured: its discovery
matches every EPUB to its own ABS ebook item (or an audiobook) and saves a suggestion.
So when a PUT /syncs/progress for an EPUB hash in the helper's map has gone through,
and BookBridge has no link for that hash, the helper makes the same Add Book submit the
owner would (audiobook None, the ABS-badged ebook), signed in as the BookBridge account
the tablet's KOSync login belongs to.

Before it creates anything it checks, every time:
  - ABS: the item is in the `ebooks` library (ABS_LIBRARY_ID), is a book with no audio
    files, and its primary ebook is an EPUB at the mapped path with the mapped size;
  - the file on the read-only mount still has that size and that KOReader hash.
Never an audiobook, never a PDF (PDFs are the helper's own path), never a direct DB write.

One attempt per hash at a time (a lock), and a failure waits before the next try
(60 s doubling to 6 h; a refused login pauses every hash for 15 min so the helper can
never run into BookBridge's 5-per-minute login limit). An explicit wait (the login pause,
a conflict, a refusal, a hash linked to another book: "until the next refresh") is not a
failure and never lengthens the backoff of the next real one. Logs carry the item id only.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from . import library as lib
from .abs_client import AbsClient, AbsError
from .bookbridge_web import BookBridgeWeb, BookBridgeWebError, SessionLost
from .partial_md5 import partial_md5

BACKOFF_FIRST = 60.0
BACKOFF_MAX = 6 * 3600.0
LOGIN_PAUSE = 15 * 60.0
DONE_TTL = 24 * 3600.0


@dataclass
class _State:
    next_try: float = 0.0
    failures: int = 0


class Refused(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class EpubLinker:
    def __init__(self, settings, abs_client: AbsClient, web: BookBridgeWeb, library: lib.PdfLibrary,
                 event, clock):
        self.s = settings
        self.abs = abs_client
        self.web = web
        self.library = library
        self.event = event
        self.clock = clock
        self._state: dict[str, _State] = {}
        self._done: dict[str, float] = {}       # hash -> until (mapped, nothing to do)
        self._locks: dict[str, asyncio.Lock] = {}
        self._login_pause_until = 0.0
        self._tasks: set[asyncio.Task] = set()

    # ---------------- scheduling ----------------
    def wanted(self, doc: str) -> lib.EpubEntry | None:
        doc = doc.lower()
        entry = self.library.lookup_epub(doc)
        if entry is None:
            return None
        now = self.clock()
        if self._done.get(doc, 0) > now:
            return None
        st = self._state.get(doc)
        if st and st.next_try > now:
            return None
        lock = self._locks.get(doc)
        if lock and lock.locked():
            return None
        return entry

    def schedule(self, doc: str) -> bool:
        """Start a background attempt for this hash if one is due. Never raises."""
        if self.wanted(doc) is None:
            return False
        task = asyncio.create_task(self.link(doc.lower(), delay=self.s.epub_link_delay_seconds))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def drain(self) -> None:
        """Wait for the running attempts (tests).

        asyncio.wait, not gather: gather on a task that has already finished completes
        eagerly and the await never yields, so the discard callback could not run and
        this loop spun forever (CI hang 2026-11-11). Pruning here does not rely on it.
        """
        while self._tasks:
            done, _ = await asyncio.wait(list(self._tasks))
            self._tasks.difference_update(done)

    async def cancel(self) -> None:
        """Stop the running attempts (shutdown). A cancelled one is simply retried later."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _fail(self, doc: str, wait: float | None = None, count: bool = True) -> None:
        """Back off. count=False: an explicit wait, not a failure (the backoff of the
        next real failure stays where it was)."""
        st = self._state.setdefault(doc, _State())
        if count:
            st.failures += 1
        delay = wait if wait is not None else min(BACKOFF_FIRST * 2 ** (st.failures - 1), BACKOFF_MAX)
        st.next_try = self.clock() + delay

    def _succeed(self, doc: str) -> None:
        self._state.pop(doc, None)
        self._done[doc] = self.clock() + DONE_TTL
        if len(self._done) > 5000:
            now = self.clock()
            self._done = {k: v for k, v in self._done.items() if v > now}

    # ---------------- one attempt ----------------
    async def link(self, doc: str, delay: float = 0.0) -> str:
        """Returns the outcome word (also logged). Never raises."""
        lock = self._locks.setdefault(doc, asyncio.Lock())
        if lock.locked():
            return "busy"
        async with lock:
            try:
                if delay:
                    # Let BookBridge finish its own discovery for this PUT first.
                    await asyncio.sleep(delay)
                return await self._link_locked(doc)
            except Exception as e:  # never take the helper down; back off and try later
                entry = self.library.lookup_epub(doc)
                self._fail(doc)
                # the class name only: exception text could carry a URL, a path or a body
                self.event("epub_mapping_failed", logging.WARNING, item=entry.item_id if entry else None,
                           reason="internal", error=type(e).__name__)
                return "failed"

    async def _link_locked(self, doc: str) -> str:
        entry = self.library.lookup_epub(doc)
        if entry is None or self.library.lookup(doc) is not None:
            return "not_epub"
        if self._done.get(doc, 0) > self.clock():
            return "mapped"
        if self._login_pause_until > self.clock():
            self._fail(doc, self._login_pause_until - self.clock(), count=False)
            return "paused"
        try:
            status = await self._with_session(lambda: self.web.mapping_status(doc, entry.item_id))
            if status == "mapped":
                self._succeed(doc)
                self.event("epub_mapping_exists", item=entry.item_id)
                return "mapped"
            if status == "other_item":
                # The hash is linked to another book: progress goes there. The owner's to
                # sort out; look again after the next refresh, never a 24 h "done".
                self._fail(doc, self.s.refresh_seconds, count=False)
                self.event("epub_mapping_other_item", logging.WARNING, item=entry.item_id)
                return "other_item"
            if status == "conflict":
                # A book for this item with another hash: the owner's to sort out.
                self._fail(doc, self.s.refresh_seconds, count=False)
                self.event("epub_mapping_conflict", logging.WARNING, item=entry.item_id)
                return "conflict"
            try:
                await self._verify(doc, entry)
            except Refused as r:
                self._fail(doc, self.s.refresh_seconds, count=False)
                self.event("epub_mapping_refused", logging.WARNING, item=entry.item_id, reason=r.reason)
                return "refused"
            await self._with_session(lambda: self.web.create_ebook_only(entry.item_id, "epub"))
            got = await self._with_session(lambda: self.web.book_hash(entry.item_id))
        except BookBridgeWebError as e:
            if e.reason in ("login_refused", "login_rate_limited"):
                self._login_pause_until = self.clock() + LOGIN_PAUSE
                self._fail(doc, LOGIN_PAUSE)
                self.event("bookbridge_login_failed", logging.WARNING, item=entry.item_id,
                           reason=e.reason, status=e.status)
                return "login_failed"
            self._fail(doc)
            self.event("epub_mapping_failed", logging.WARNING, item=entry.item_id, reason=e.reason, status=e.status)
            return "failed"
        if got == doc:
            self._succeed(doc)
            self.event("epub_mapping_created", item=entry.item_id, status=200)
            return "created"
        # BookBridge saved the book but hashed the file differently (or not at all).
        self._fail(doc, BACKOFF_MAX)
        self.event("epub_mapping_hash_mismatch", logging.WARNING, item=entry.item_id)
        return "hash_mismatch"

    async def _with_session(self, call):
        """Sign in when needed; on a lost session or a refused CSRF token sign in once more.

        The failed call already forgot its session, so ensure_session() signs in again
        unless a concurrent attempt has just done so (then its session is reused)."""
        await self.web.ensure_session()
        try:
            return await call()
        except SessionLost:
            pass
        except BookBridgeWebError as e:
            if e.reason != "csrf_refused":
                raise
        await self.web.ensure_session()
        return await call()

    async def _verify(self, doc: str, entry: lib.EpubEntry) -> None:
        try:
            item = await self.abs.get_item(entry.item_id)
        except AbsError:
            raise BookBridgeWebError("abs_error") from None
        if not isinstance(item, dict):
            raise Refused("abs_item_missing")
        if item.get("libraryId") != self.s.abs_library_id:
            raise Refused("not_in_ebooks_library")
        if item.get("mediaType") not in (None, "book"):
            raise Refused("not_a_book")
        media = item.get("media") or {}
        if media.get("audioFiles") or media.get("tracks") or media.get("numAudioFiles"):
            raise Refused("has_audio")
        ebook = media.get("ebookFile") or {}
        meta = ebook.get("metadata") or {}
        if ebook.get("ebookFormat") != "epub":
            raise Refused("primary_not_epub")
        if meta.get("path") != entry.abs_path:
            raise Refused("primary_path_changed")
        if meta.get("size") != entry.size:
            raise Refused("abs_size_differs")
        real = lib.safe_path(self.library.root, entry.abs_path)
        if real is None or real != entry.real_path:
            raise Refused("file_missing")
        try:
            size = os.stat(real).st_size
            digest = await asyncio.to_thread(partial_md5, real)
        except OSError:
            raise Refused("file_unreadable") from None
        if size != entry.size or digest != doc:
            raise Refused("file_changed")
