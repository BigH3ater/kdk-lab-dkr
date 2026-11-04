#!/usr/bin/env python3
# ruff: noqa
"""Offline unit tests for the LazyLibrarian -> Audiobookshelf hook (abs-notify.py).

No network, no LazyLibrarian: series rows come from a throwaway SQLite file
with LazyLibrarian's member/series/seriesauthors/books schema. Run:

    python3 -I -m pytest -q stacks/media/lazylibrarian/tests

Covers:
  * series pick: the author's own series beats a foreign/split-volume one, a
    book whose only series is foreign gets none (A Feast for Crows, 2026-11-03),
    and an ignored series loses to an active one;
  * SeriesDisplay with several '<br>' entries skips a foreign-script one;
  * the OPF folder steps up out of CD1/ / Disc 2/ to the book folder.
"""

import importlib.util
import os
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

_HOOK = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "abs-notify.py"
_spec = importlib.util.spec_from_file_location("abs_notify", _HOOK)
abs_notify = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(abs_notify)

ASOIAF = "A Song of Ice and Fire"
CN = "冰與火之歌 Split-Volume edition"


def _make_db(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE books (BookID TEXT, AuthorID TEXT, BookName TEXT);
        CREATE TABLE series (SeriesID TEXT UNIQUE, SeriesName TEXT, Status TEXT,
                             Have INTEGER, Total INTEGER);
        CREATE TABLE member (SeriesID TEXT, BookID TEXT, WorkID TEXT, SeriesNum TEXT);
        CREATE TABLE seriesauthors (SeriesID TEXT, AuthorID TEXT);
        INSERT INTO books VALUES ('feast', 'grrm', 'A Feast for Crows');
        INSERT INTO books VALUES ('clash', 'grrm', 'A Clash of Kings');
        INSERT INTO books VALUES ('x', 'a1', 'Some Book');
        INSERT INTO series VALUES ('HC125742', '冰與火之歌 Split-Volume edition', 'Paused', 1, 1);
        INSERT INTO series VALUES ('HC981', 'A Song of Ice and Fire', 'Paused', 3, 33);
        INSERT INTO series VALUES ('S-ign', 'Ignored Omnibus', 'Ignored', 0, 50);
        INSERT INTO series VALUES ('S-ok', 'Real Series', 'Active', 1, 5);
        INSERT INTO seriesauthors VALUES ('HC981', 'grrm');
        INSERT INTO seriesauthors VALUES ('HC125742', 'grrm');
        -- Clash: the foreign series row comes FIRST by rowid (the old bug).
        INSERT INTO member VALUES ('HC125742', 'clash', '', '2.1');
        INSERT INTO member VALUES ('HC981', 'clash', '', '2');
        -- Feast: only the foreign split-volume row exists.
        INSERT INTO member VALUES ('HC125742', 'feast', '', '4.2');
        INSERT INTO member VALUES ('S-ign', 'x', '', '9');
        INSERT INTO member VALUES ('S-ok', 'x', '', '3');
        """
    )
    con.commit()
    con.close()


class SeriesFromDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "lazylibrarian.db")
        _make_db(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def lookup(self, book_id, title):
        with mock.patch.dict(os.environ, {"LL_DB": self.db}):
            return abs_notify.series_from_db(book_id, title)

    def test_main_series_beats_earlier_foreign_row(self):
        self.assertEqual(self.lookup("clash", "A Clash of Kings"), (ASOIAF, "2"))

    def test_only_foreign_series_gives_none(self):
        self.assertEqual(self.lookup("feast", "A Feast for Crows"), ("", ""))

    def test_foreign_title_keeps_foreign_series(self):
        self.assertEqual(self.lookup("feast", "冰與火之歌"), (CN, "4.2"))

    def test_ignored_series_loses(self):
        self.assertEqual(self.lookup("x", "Some Book"), ("Real Series", "3"))

    def test_missing_db_is_harmless(self):
        with mock.patch.dict(os.environ, {"LL_DB": "/nonexistent/ll.db"}), \
             mock.patch.object(abs_notify, "log"):
            self.assertEqual(abs_notify.series_from_db("clash", "x"), ("", ""))


class ParseSeries(unittest.TestCase):
    def test_skips_foreign_entry(self):
        disp = f"{CN} #4.2<br>{ASOIAF} #4"
        self.assertEqual(abs_notify.parse_series(disp, "A Feast for Crows"), (ASOIAF, "4"))

    def test_single(self):
        self.assertEqual(abs_notify.parse_series("Expeditionary Force #6", "Mavericks"),
                         ("Expeditionary Force", "6"))

    def test_none_literal(self):
        self.assertEqual(abs_notify.parse_series("None", "x"), ("", ""))

    def test_accented_latin_is_not_foreign(self):
        self.assertFalse(abs_notify.is_foreign_script("Brené Brown - Führung wagen"))
        self.assertTrue(abs_notify.is_foreign_script("Песнь льда и огня"))


class BookFolder(unittest.TestCase):
    ROOT = "/data/media/books/audiobook/George R.R. Martin/A Song of Ice and Fire/A Clash of Kings"

    def test_flat(self):
        self.assertEqual(abs_notify.book_folder(f"{self.ROOT}/01.mp3"), self.ROOT)

    def test_cd1(self):
        self.assertEqual(abs_notify.book_folder(f"{self.ROOT}/CD1/01.mp3"), self.ROOT)

    def test_disc_variants(self):
        for d in ("Disc 2", "disk_03", "Part 1", "CD 10"):
            self.assertEqual(abs_notify.book_folder(f"{self.ROOT}/{d}/a.mp3"), self.ROOT, d)

    def test_title_with_number_not_stripped(self):
        p = "/data/media/books/audiobook/Craig Alanson/Expeditionary Force/Part 7 Title"
        self.assertEqual(abs_notify.book_folder(f"{p}/a.mp3"), p)

    def test_empty(self):
        self.assertEqual(abs_notify.book_folder(""), "")


class WriteOpfInBookFolder(unittest.TestCase):
    def test_main_writes_opf_beside_book_not_in_cd1(self):
        with tempfile.TemporaryDirectory() as root:
            book = os.path.join(root, "Author", "Series", "Title")
            os.makedirs(os.path.join(book, "CD1"))
            argv = ["abs-notify.py", "AuxInfo", "AudioBook", "BookID", "b1",
                    "BookName", "Title", "AudioFile", os.path.join(book, "CD1", "01.mp3"),
                    "SeriesDisplay", "Series #2"]
            env = {"ABS_AUDIO_ROOT": root}
            with mock.patch.object(abs_notify.sys, "argv", argv), \
                 mock.patch.dict(os.environ, env), \
                 mock.patch.object(abs_notify, "trigger_scan"), \
                 mock.patch.object(abs_notify, "log"):
                self.assertEqual(abs_notify.main(), 0)
            self.assertTrue(os.path.isfile(os.path.join(book, "metadata.opf")))
            self.assertFalse(os.path.exists(os.path.join(book, "CD1", "metadata.opf")))
            opf = open(os.path.join(book, "metadata.opf"), encoding="utf-8").read()
            self.assertIn('content="Series"', opf)
            self.assertIn('content="2"', opf)
            self.assertIn("<dc:creator opf:role=\"aut\">Author</dc:creator>", opf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
