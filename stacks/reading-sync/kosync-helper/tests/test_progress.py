import pytest

from kosync_helper import progress as p


def test_kosync_page_to_abs():
    payload, clamped = p.kosync_to_abs(40, 200)
    assert payload == {"ebookLocation": "40", "ebookProgress": 39 / 200}
    assert not clamped


def test_first_page_is_zero_progress():
    assert p.kosync_to_abs(1, 200)[0]["ebookProgress"] == 0


def test_last_page_marks_finished():
    payload, _ = p.kosync_to_abs(200, 200)
    assert payload == {"ebookLocation": "200", "ebookProgress": 199 / 200, "isFinished": True}


def test_page_past_end_is_clamped_and_finished():
    payload, clamped = p.kosync_to_abs(205, 200)
    assert clamped and payload["ebookLocation"] == "200" and payload["isFinished"] is True


def test_unknown_page_count_sends_location_only():
    assert p.kosync_to_abs(12, None)[0] == {"ebookLocation": "12"}


def test_never_unfinishes():
    assert "isFinished" not in p.kosync_to_abs(3, 200)[0]


def test_abs_to_kosync():
    out = p.abs_to_kosync({"ebookLocation": "55", "ebookProgress": 54 / 200, "lastUpdate": 1_800_000_123_456}, 200)
    assert out == {"progress": "55", "percentage": 0.275, "timestamp": 1_800_000_123}


def test_abs_to_kosync_matches_koreader_rounding():
    # KOReader: Math.roundPercent(page / pages) = floor(x * 10000) / 10000
    out = p.abs_to_kosync({"ebookLocation": "1", "lastUpdate": 0}, 3)
    assert out["percentage"] == 0.3333


def test_abs_last_page_round_trip():
    payload, _ = p.kosync_to_abs(200, 200)
    assert p.abs_to_kosync(payload, 200)["progress"] == "200"
    assert p.abs_to_kosync(payload, 200)["percentage"] == 1.0


@pytest.mark.parametrize("page", range(1, 11))
def test_ten_pages_round_trip(page):
    payload, _ = p.kosync_to_abs(page, 10)
    assert p.parse_page(p.abs_to_kosync(payload, 10)["progress"]) == page


@pytest.mark.parametrize("progress", [None, {}, {"ebookLocation": None},
                                      {"ebookLocation": "epubcfi(/6/4!/4/2)"}, {"ebookLocation": "0"}])
def test_abs_without_a_page(progress):
    assert p.abs_to_kosync(progress, 10) is None


def test_abs_unknown_page_count_uses_ebook_progress():
    assert p.abs_to_kosync({"ebookLocation": "5", "ebookProgress": 0.4}, None)["percentage"] == 0.4


@pytest.mark.parametrize("value,expected", [(7, 7), ("7", 7), (" 12 ", 12), (3.0, 3)])
def test_parse_page_ok(value, expected):
    assert p.parse_page(value) == expected


@pytest.mark.parametrize("value", [0, -1, 100_001, "1e3", "7a", "", None, True, 2.5, [], "/body/DocFragment[1]"])
def test_parse_page_rejects(value):
    with pytest.raises(p.InvalidInput):
        p.parse_page(value)


@pytest.mark.parametrize("value", [-0.1, 1.5, float("nan"), "0.5", True])
def test_parse_percentage_rejects(value):
    with pytest.raises(p.InvalidInput):
        p.parse_percentage(value)


def test_pages_from_percentage():
    assert p.pages_from_percentage(40, p.round_percent(40 / 321)) == 321
    assert p.pages_from_percentage(5, None) is None
