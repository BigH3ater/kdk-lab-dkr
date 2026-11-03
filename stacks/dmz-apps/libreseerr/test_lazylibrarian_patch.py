#!/usr/bin/env python3
"""Offline tests for the local libreseerr v0.9.1 LazyLibrarian-client patch.

Covers both patched behaviours (stdlib unittest only, no network):
  * result ranking in ``rank_books`` demotes box sets / bundles and prefers the
    single requested book (the real "A Game of Thrones" list, and "Columbus Day"
    beating unrelated matches);
  * ``add_book`` queues ``type=AudioBook`` for an audiobook client and
    ``type=eBook`` for an ebook client (``_get`` is mocked, nothing leaves the
    process).

Run:  python3 -m unittest -v  (from this directory)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lazylibrarian  # noqa: E402
from lazylibrarian import LazyLibrarianClient, rank_books  # noqa: E402


# The real list LazyLibrarian+Hardcover returns first for this query, plus the
# single correct book (bookid 644) buried after the bundles.
GOT_QUERY = "A Game of Thrones George R. R. Martin"
GOT_RECORDS = [
    {"bookname": "A Song of Ice and Fire Audiobook Bundle (Books 1-5)",
     "authorname": "George R.R. Martin", "bookid": "9001"},
    {"bookname": "A Song of Ice and Fire: The Complete Box Set of All 7 Books",
     "authorname": "George R.R. Martin", "bookid": "9002"},
    {"bookname": "A Game of Thrones Box Set: 7 Books Set",
     "authorname": "George R.R. Martin", "bookid": "9003"},
    {"bookname": "A Game of Thrones", "authorname": "George R.R. Martin",
     "bookid": "644"},
]

COLUMBUS_QUERY = "Columbus Day Craig Alanson"
COLUMBUS_RECORDS = [
    {"bookname": "Columbus: The Four Voyages, 1492-1504",
     "authorname": "Laurence Bergreen", "bookid": "201"},
    {"bookname": "The Pirate's Wish", "authorname": "Cassandra Rose Clarke",
     "bookid": "202"},
    {"bookname": "Columbus Day", "authorname": "Craig Alanson", "bookid": "101"},
]


class _Recorder:
    """Stand-in for LazyLibrarianClient._get that records calls, hits no network."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **params):
        self.calls.append((cmd, params))
        return {}

    def queue_type_for(self):
        for cmd, params in self.calls:
            if cmd == "queueBook":
                return params.get("type")
        return None


class RankBooksTests(unittest.TestCase):
    def test_single_book_beats_bundles(self):
        ranked = rank_books(GOT_RECORDS, GOT_QUERY)
        self.assertEqual(ranked[0]["bookid"], "644")
        self.assertEqual(ranked[0]["bookname"], "A Game of Thrones")

    def test_bundles_are_detected(self):
        for rec in GOT_RECORDS[:3]:
            self.assertTrue(lazylibrarian.is_bundle(rec["bookname"]),
                            f"should be a bundle: {rec['bookname']}")
        self.assertFalse(lazylibrarian.is_bundle("A Game of Thrones"))

    def test_nothing_is_dropped(self):
        ranked = rank_books(GOT_RECORDS, GOT_QUERY)
        self.assertCountEqual([r["bookid"] for r in ranked],
                              [r["bookid"] for r in GOT_RECORDS])

    def test_columbus_day_beats_unrelated(self):
        ranked = rank_books(COLUMBUS_RECORDS, COLUMBUS_QUERY)
        self.assertEqual(ranked[0]["bookid"], "101")
        self.assertEqual(ranked[0]["bookname"], "Columbus Day")

    def test_fuzz_fields_break_ties(self):
        # Two non-bundle, non-matching titles: higher highest_fuzz wins.
        recs = [
            {"bookname": "Zzz", "authorname": "X", "bookid": "1", "highest_fuzz": 10},
            {"bookname": "Yyy", "authorname": "X", "bookid": "2", "highest_fuzz": 90},
        ]
        ranked = rank_books(recs, "something unrelated")
        self.assertEqual(ranked[0]["bookid"], "2")


class QueueTypeTests(unittest.TestCase):
    def _add(self, queue_type):
        client = (LazyLibrarianClient("http://ll.example", "key", queue_type=queue_type)
                  if queue_type is not None
                  else LazyLibrarianClient("http://ll.example", "key"))
        rec = _Recorder()
        client._get = rec
        client.add_book({"foreignBookId": "644", "title": "A Game of Thrones"}, 1, "/books")
        return rec

    def test_audiobook_client_queues_audiobook(self):
        rec = self._add("AudioBook")
        self.assertEqual(rec.queue_type_for(), "AudioBook")

    def test_ebook_client_queues_ebook(self):
        rec = self._add("eBook")
        self.assertEqual(rec.queue_type_for(), "eBook")

    def test_default_is_ebook(self):
        rec = self._add(None)  # no queue_type kwarg -> upstream default
        self.assertEqual(rec.queue_type_for(), "eBook")


if __name__ == "__main__":
    unittest.main()
