"""abs_collections.py: Reading List parsing, title matching, the plan diff, and
idempotency against an in-memory fake of the ABS 2.37.2 collection API
(semantics copied from server/controllers/CollectionController.js)."""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import abs_collections as ac

EB, AU = ac.EBOOKS_LIBRARY, ac.AUDIOBOOKS_LIBRARY

READING_LIST = """\
# Reading List

Intro text | with a pipe that is not a table row.

## Round 1: one book per section

| # | Book | Edition | Section | Status | Read | Takeaway |
|---|---|---|---|---|---|---|
| 1 | Make It Stick (Brown, Roediger, McDaniel) | 2014 | Studying | ABS epub | whole (8 ch.) |  |
| 2 | A Philosophy of Software Design (Ousterhout) | 2nd ed., 2021 | Code craft and testing | ABS epub | whole |  |
| 3 | Python Testing with pytest (Okken) | 2nd ed., 2022 | Languages | ABS epub | Part I |  |
| 4 | Designing Data-Intensive Applications (Kleppmann & Riccomini) | 2nd ed., 2026 | Architecture | ABS epub | ch. 1-5 |  |
| 5 | Escaping the Build Trap (Perri) | 2018 | Product | NEW | whole (short) |  |
| 6 | Computer Networking: A Top-Down Approach (Kurose & Ross) | 8th ed., 2021 | Networking | ABS epub | ch. 4-5 |  |
| 7 | Don't Make Me Think, Revisited (Krug) | 3rd ed., 2014 (still current) | Web | NEW | whole |  |
| 8 | Site Reliability Engineering (Beyer et al.) | 2016 | Reliability | ABS epub | ch. 1 |  |
| 9 | The Staff Engineer's Path (Reilly) | 2022 | Leadership | ABS epub | whole |  |
| 10 | Co-Intelligence (Mollick) | 2024 | AI | missing | whole (short) |  |

## Then, in this order

| # | Book | Edition | Section | Status | Read | Takeaway |
|---|---|---|---|---|---|---|
| 11 | TCP/IP Illustrated, Vol. 1 (Fall & Stevens) | 2nd ed., 2011 | Networking | ABS epub | ch. 1 |  |
| 12 | 100 Go Mistakes and How to Avoid Them (Harsanyi) | 2022 | Languages | ABS epub | ch. 2-3 |  |
| 13 | Fundamentals of Software Architecture (Richards & Ford) | 2nd ed., 2025 | Architecture | missing | Part I |  |
| 14 | The Pragmatic Programmer (Thomas & Hunt) | 20th anniv. ed., 2019 | Code craft | ABS epub | ch. 1-2 |  |
| 15 | Fluent Python (Ramalho) | 2nd ed., 2022 | Languages | ABS epub | ch. 1-3 |  |
| 16 | Staff Engineer: Leadership Beyond the Management Track (Larson) | 2021 | Leadership | missing | Part I |  |

## Audio queue (commute, alongside the list)

1. *Accelerate* (Forsgren, Humble, Kim): the delivery-performance evidence (missing; Libby).
2. *The Phoenix Project* (Kim, Behr, Spafford): novel (audiobook already in the library).
3. *Turn the Ship Around!* (Marquet): intent-based leadership (ABS epub; no audio yet).

## Reference shelf (not scheduled)

| Book | Edition | Status |
|---|---|---|
| Peak | 2016, Ericsson & Pool | ABS epub |
"""


def raw(id_, title, author, isbn=None, **extra):
    return {"id": id_, "media": {"metadata": {"title": title, "authorName": author, "isbn": isbn}},
            **extra}


EBOOK_ITEMS = [
    raw("e-stick", "Make It Stick", "Peter C. Brown"),
    raw("e-apsd", "A Philosophy of Software Design", "John. Ousterhout"),
    raw("e-pytest", "Python Testing with pytest, Second Edition: Simple, Rapid, Effective, and Scalable",
        "Okken, Brian, Dvorak, Katharine", "9781680509441"),
    raw("e-ddia1", "Designing Data-Intensive Applications", "Martin Kleppmann"),
    raw("e-ddia2", "Designing Data-Intensive Applications 2nd Edition", "Martin Kleppmann", "9781098119065"),
    raw("e-net", "Computer Networking: A Top-Down Approach", "Keith W. Ross"),
    raw("e-sre", "Site Reliability Engineering: How Google Runs Production Systems", "Betsy Beyer"),
    raw("e-srw", "The Site Reliability Workbook: Practical Ways to Implement Sre", "Betsy Beyer"),
    raw("e-staff", "The Staff Engineer's Path", "Tanya Reilly", "9781098118679"),
    raw("e-tcp", "TCP-IP Illustrated Volume 1 2nd Edition", "Kevin Fall"),
    raw("e-100go", "100 Go Mistakes", "Teiva Harsanyi"),
    raw("e-fsa", "Fundamentals of Software Architecture", "Mark Richards"),
    raw("e-prag", "The Pragmatic Programmer 20th Anniversary Edition", "Andrew Hunt", "9780135957059"),
    raw("e-fluent", "Fluent Python 2nd Edition", "Luciano Ramalho"),
    raw("e-gone", "Make It Stick", "Peter C. Brown", isMissing=True),
]
AUDIO_ITEMS = [
    raw("a-phoenix", "The Phoenix Project", "Gene Kim"),
    raw("a-unicorn", "The Unicorn Project", "Gene Kim"),
]


def items(raws):
    return [ac.Item.from_api(r) for r in raws if not r.get("isMissing")]


@pytest.fixture
def lists():
    return ac.parse_reading_list(READING_LIST)


def by_num(lists, key="table"):
    return {e.num: e for e in lists[key]}


# ------------------------------------------------------------------ parsing

def test_parse_tables(lists):
    table = lists["table"]
    assert [e.num for e in table] == list(range(1, 17))
    e = table[0]
    assert (e.title, e.authors, e.edition, e.status) == (
        "Make It Stick", ["brown", "roediger", "mcdaniel"], "2014", "ABS epub")
    assert table[3].authors == ["kleppmann", "riccomini"]
    assert table[7].authors == ["beyer"]                       # "et al." dropped
    assert table[15].title == "Staff Engineer: Leadership Beyond the Management Track"
    assert [e.num for e in lists["round1"]] == list(range(1, 11))


def test_round1_is_every_row_of_its_table_whatever_the_heading_says():
    longer = READING_LIST.replace("## Round 1: one book per section",
                                  "## Round 1: one book per section, plus a Python on-ramp")
    extra = ("| 11 | Python Crash Course (Matthes) | 3rd ed., 2023 | Languages | NEW | ch. 1-11 |  |\n"
             "| 12 | Test-Driven Development: By Example (Beck) | 2002 | Code craft | missing | Parts I-II |  |\n")
    longer = longer.replace("\n\n## Then, in this order", "\n" + extra + "\n## Then, in this order")
    lists = ac.parse_reading_list(longer.replace("| 11 | TCP", "| 13 | TCP"))
    assert [e.num for e in lists["round1"]] == list(range(1, 13))
    assert lists["round1"][10].title == "Python Crash Course"


def test_round1_heading_needs_the_colon():
    with pytest.raises(ValueError, match="Round 1"):
        ac.parse_reading_list(READING_LIST.replace("## Round 1: one", "## Round 10 one"))


def test_parse_audio(lists):
    audio = lists["audio"]
    assert [(e.num, e.title) for e in audio] == [
        (1, "Accelerate"), (2, "The Phoenix Project"), (3, "Turn the Ship Around!")]
    assert audio[0].authors == ["forsgren", "humble", "kim"]
    assert audio[0].status == "missing" and audio[1].status == ""


def test_reference_shelf_ignored(lists):
    assert all(e.title != "Peak" for e in lists["table"])


def test_missing_heading_is_an_error():
    with pytest.raises(ValueError, match="Audio queue"):
        ac.parse_reading_list(READING_LIST.split("## Audio queue")[0])


def test_isbn_in_row_parsed():
    [e] = ac.parse_table(["| 1 | Peak (Ericsson) ISBN 978-0-544-45625-9 | 2016 | x | calibre |"])
    assert e.isbns == ["9780544456259"]


# ------------------------------------------------------------------ matching

@pytest.mark.parametrize("a,b", [
    ("Python Testing with pytest, Second Edition: Simple, Rapid", "Python Testing with pytest"),
    ("TCP-IP Illustrated Volume 1 2nd Edition", "TCP/IP Illustrated, Vol. 1"),
    ("The Pragmatic Programmer 20th Anniversary Edition", "The Pragmatic Programmer"),
    ("Turn The Ship Around!", "Turn the Ship Around!"),
    ("Everything Is F*cked (9780062888471)", "Everything Is F*cked"),
    ("Hypermedia Systems", "Hypermedia  systems."),
])
def test_norm_title_equal(a, b):
    assert ac.norm_title(a) == ac.norm_title(b)


def test_norm_title_keeps_distinct_books_apart():
    assert ac.norm_title("The Staff Engineer's Path") != ac.norm_title("Staff Engineer: Leadership")
    assert ac.norm_title("Site Reliability Engineering") != ac.norm_title("The Site Reliability Workbook")


def test_matches(lists):
    eb = items(EBOOK_ITEMS)
    got = {e.num: ac.match(e, eb) for e in lists["table"]}
    assert got[1].item.id == "e-stick" and got[1].how == "title" and not got[1].doubts
    assert got[3].item.id == "e-pytest"
    assert got[4].item.id == "e-ddia2"                         # edition tie-break
    assert got[6].item.id == "e-net"
    assert got[8].item.id == "e-sre"
    assert got[11].item.id == "e-tcp"
    assert got[14].item.id == "e-prag"
    assert got[15].item.id == "e-fluent"


def test_non_matches_are_reported_not_guessed(lists):
    eb = items(EBOOK_ITEMS)
    n = by_num(lists)
    for num, how in ((5, "list-says-new"), (7, "list-says-new"),
                     (10, "list-says-missing"), (16, "list-says-missing")):
        m = ac.match(n[num], eb)
        assert m.item is None and m.how == how
    assert ac.match(ac.Entry(1, "Not In ABS", ["nobody"], status="ABS epub"), eb).how == "none"


def test_prefix_needs_author_and_is_doubtful(lists):
    eb = items(EBOOK_ITEMS)
    e = by_num(lists)[12]
    m = ac.match(e, eb)
    assert m.item.id == "e-100go" and m.how == "prefix+author" and m.doubts
    other = ac.Entry(num=1, title=e.title, authors=["someoneelse"])
    assert ac.match(other, eb).item is None


def test_status_missing_is_never_matched_even_if_abs_has_the_title(lists):
    # a stray older edition in ABS must not join the collections while the list says missing
    m = ac.match(by_num(lists)[13], items(EBOOK_ITEMS))
    assert m.item is None and m.how == "list-says-missing"


def test_ambiguous_is_skipped():
    eb = items([raw("x1", "Deep Work", "Cal Newport"), raw("x2", "Deep Work", "Cal Newport")])
    m = ac.match(ac.Entry(1, "Deep Work", ["newport"]), eb)
    assert m.item is None and m.how == "ambiguous"


def test_author_breaks_tie():
    eb = items([raw("x1", "Code", "Charles Petzold"), raw("x2", "Code", "Someone Else")])
    assert ac.match(ac.Entry(1, "Code", ["petzold"]), eb).item.id == "x1"


def test_explicit_other_edition_is_not_a_match():
    eb = items([raw("x1", "Fluent Python 1st Edition", "Luciano Ramalho")])
    m = ac.match(ac.Entry(1, "Fluent Python", ["ramalho"], edition="2nd ed., 2022"), eb)
    assert m.item is None and m.how == "edition-mismatch"


def test_isbn_wins_over_title():
    eb = items([raw("x1", "Some Other Title", "A. Writer", "9780544456259"),
                raw("x2", "Peak", "K. Anders Ericsson")])
    m = ac.match(ac.Entry(1, "Peak", ["ericsson"], isbns=["9780544456259"]), eb)
    assert m.item.id == "x1" and m.how == "isbn"


# ------------------------------------------------------------------ fake ABS

class FakeAbs:
    """Mirror of the ABS 2.37.2 CollectionController semantics that matter here."""

    def __init__(self, libraries):
        self.libraries = libraries                   # {lib_id: [raw item]}
        self.cols = {}                               # id -> {name, libraryId, books:[ids]}
        self._ids = (f"col-{i}" for i in itertools.count(1))
        self.writes = []

    def lib_of(self, item_id):
        return next(lib for lib, rs in self.libraries.items() if any(r["id"] == item_id for r in rs))

    # AbsClient surface
    def library_items(self, lib):
        return [ac.Item.from_api(r) for r in self.libraries[lib] if not r.get("isMissing")]

    def collections(self):
        return [{"id": cid, "name": c["name"], "libraryId": c["libraryId"],
                 "books": [{"id": b} for b in c["books"]]} for cid, c in self.cols.items()]

    def send(self, op):
        self.writes.append(op)
        b, p = op.body, op.path
        if op.method == "POST" and p == "/collections":
            assert b["books"], "ABS 400: No books"
            assert all(self.lib_of(i) == b["libraryId"] for i in b["books"]), "ABS 400: Invalid books"
            cid = next(self._ids)
            self.cols[cid] = {"name": b["name"], "libraryId": b["libraryId"], "books": list(b["books"])}
            return
        col = self.cols[p.split("/")[2]]
        if op.method == "POST" and p.endswith("/batch/add"):
            new = [i for i in b["books"] if self.lib_of(i) == col["libraryId"] and i not in col["books"]]
            col["books"] += sorted(new, reverse=True)           # DB order, not request order
        elif op.method == "POST" and p.endswith("/batch/remove"):
            col["books"] = [i for i in col["books"] if i not in b["books"]]
        elif op.method == "PATCH":
            def idx(i):
                return b["books"].index(i) if i in b["books"] else -1   # JS findIndex
            col["books"] = sorted(col["books"], key=idx)
        else:
            raise AssertionError(f"unexpected op {op}")


def run(fake, lists):
    libs = {lib: fake.library_items(lib) for lib in (EB, AU)}
    plans = ac.build_plans(lists, libs, fake.collections())
    return plans, ac.apply(fake, plans)


def books(fake, name):
    [c] = [c for c in fake.cols.values() if c["name"] == name]
    return c["books"]


def cid_of(fake, name):
    return next(k for k, c in fake.cols.items() if c["name"] == name)


ROUND1_IDS = ["e-stick", "e-apsd", "e-pytest", "e-ddia2", "e-net", "e-sre", "e-staff"]
ORDER_IDS = ROUND1_IDS + ["e-tcp", "e-100go", "e-prag", "e-fluent"]           # e-fsa: list says missing


def test_create_then_idempotent(lists):
    fake = FakeAbs({EB: list(EBOOK_ITEMS), AU: list(AUDIO_ITEMS)})
    _, n = run(fake, lists)
    assert n == 3 and all(op.path == "/collections" for op in fake.writes)
    assert books(fake, "Round 1") == ROUND1_IDS
    assert books(fake, "Reading order") == ORDER_IDS
    assert books(fake, "Audio queue") == ["a-phoenix"]
    fake.writes.clear()
    plans, n = run(fake, lists)
    assert n == 0 and fake.writes == [] and all(not p.ops for p in plans)


def test_no_matches_creates_nothing(lists):
    fake = FakeAbs({EB: [], AU: []})
    _, n = run(fake, lists)
    assert n == 0 and fake.cols == {}


def test_reorder_add_remove(lists):
    fake = FakeAbs({EB: EBOOK_ITEMS + [raw("e-old", "Dropped Book", "X")], AU: list(AUDIO_ITEMS)})
    run(fake, lists)
    cid = cid_of(fake, "Reading order")
    # drift: a book that left the list, a wrong order, and a book missing from the collection
    fake.cols[cid]["books"] = ["e-old", "e-net", "e-stick"] + [
        i for i in ORDER_IDS if i not in ("e-net", "e-stick", "e-fluent", "e-prag")]
    fake.writes.clear()
    run(fake, lists)
    kinds = [(op.method, op.path.rsplit("/", 1)[-1]) for op in fake.writes]
    assert kinds == [("POST", "remove"), ("POST", "add"), ("PATCH", cid)]
    assert fake.writes[0].body == {"books": ["e-old"]}
    assert fake.writes[1].body == {"books": ["e-prag", "e-fluent"]}
    assert books(fake, "Reading order") == ORDER_IDS
    fake.writes.clear()
    assert run(fake, lists)[1] == 0


def test_reorder_only(lists):
    fake = FakeAbs({EB: list(EBOOK_ITEMS), AU: list(AUDIO_ITEMS)})
    run(fake, lists)
    fake.cols[cid_of(fake, "Round 1")]["books"] = list(reversed(ROUND1_IDS))
    fake.writes.clear()
    run(fake, lists)
    assert [op.method for op in fake.writes] == ["PATCH"]
    assert books(fake, "Round 1") == ROUND1_IDS


def test_new_book_arrives_joins_in_list_order(lists):
    fake = FakeAbs({EB: list(EBOOK_ITEMS), AU: list(AUDIO_ITEMS)})
    run(fake, lists)
    fake.libraries[EB].append(raw("e-trap", "Escaping the Build Trap", "Melissa Perri"))
    fake.writes.clear()
    run(fake, lists)                                  # list still says NEW: not added yet
    assert "e-trap" not in books(fake, "Round 1")
    lists = ac.parse_reading_list(READING_LIST.replace("| Product | NEW |", "| Product | ABS epub |"))
    run(fake, lists)
    want = ["e-stick", "e-apsd", "e-pytest", "e-ddia2", "e-trap", "e-net", "e-sre", "e-staff"]
    assert books(fake, "Round 1") == want
    assert books(fake, "Reading order")[:8] == want
    fake.writes.clear()
    assert run(fake, lists)[1] == 0


def test_book_leaving_list_is_removed(lists):
    fake = FakeAbs({EB: list(EBOOK_ITEMS), AU: list(AUDIO_ITEMS)})
    run(fake, lists)
    shorter = ac.parse_reading_list(READING_LIST.replace(
        "| 1 | Make It Stick (Brown, Roediger, McDaniel)", "| 1 | Something Else (Nobody)"))
    fake.writes.clear()
    run(fake, shorter)
    assert [op.path.rsplit("/", 1)[-1] for op in fake.writes if "Round" not in op.path][:1] == ["remove"]
    assert books(fake, "Round 1") == ROUND1_IDS[1:]


def test_duplicate_collection_names_stop():
    cols = [{"id": "a", "name": "Round 1", "libraryId": EB, "books": []},
            {"id": "b", "name": "Round 1", "libraryId": EB, "books": []}]
    with pytest.raises(RuntimeError, match="2 collections"):
        ac.plan_collection("Round 1", EB, [], cols)


def test_same_name_other_library_is_ignored():
    cols = [{"id": "a", "name": "Round 1", "libraryId": AU, "books": [{"id": "a-phoenix"}]}]
    m = ac.Match(ac.Entry(1, "Make It Stick", []), ac.Item("e-stick", "Make It Stick", ""), "title")
    p = ac.plan_collection("Round 1", EB, [m], cols)
    assert p.current is None and p.ops[0].path == "/collections"


def test_main_dry_run_makes_no_writes(tmp_path, monkeypatch, capsys):
    note = tmp_path / "Reading List.md"
    note.write_text(READING_LIST, encoding="utf-8")
    fake = FakeAbs({EB: list(EBOOK_ITEMS), AU: list(AUDIO_ITEMS)})
    monkeypatch.setattr(ac, "AbsClient", lambda base, token: fake)
    monkeypatch.setenv("ABS_TOKEN", "secret-token-value")
    assert ac.main(["--list", str(note)]) == 0
    out = capsys.readouterr().out
    assert fake.writes == [] and "Dry run" in out and "not in ABS yet" in out
    assert "secret-token-value" not in out
    assert ac.main(["--list", str(note), "--apply"]) == 0
    assert len(fake.writes) == 3
    fake.writes.clear()
    assert ac.main(["--list", str(note), "--apply"]) == 0
    assert fake.writes == []


def test_main_needs_token(monkeypatch, tmp_path):
    monkeypatch.delenv("ABS_TOKEN", raising=False)
    assert ac.main(["--list", str(tmp_path / "x.md")]) == 2
