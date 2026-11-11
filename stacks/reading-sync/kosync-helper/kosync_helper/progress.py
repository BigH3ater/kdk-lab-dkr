"""PDF position mapping between KOReader kosync and Audiobookshelf.

KOReader (paged documents, plugins/kosync.koplugin): `progress` = the top page number
(ReaderPaging:getTopPage), `percentage` = floor(page / pages * 10000) / 10000.

Audiobookshelf web reader and app (PdfReader.vue): `ebookLocation` = the 1-based page as
a string, `ebookProgress` = (page - 1) / pages, reopens at Number(ebookLocation).

The same file has the same page count on both sides, so page N maps to page N.
"""
from __future__ import annotations

import math
import re
from typing import Any

MAX_PAGES = 100_000
_INT_RE = re.compile(r"^\s*([0-9]{1,6})\s*$")


class InvalidInput(ValueError):
    pass


def round_percent(value: float) -> float:
    """KOReader's Math.roundPercent: floor to 4 decimals."""
    return math.floor(value * 10000) / 10000


def parse_page(value: Any) -> int:
    """A page number from a kosync `progress` or an ABS `ebookLocation`."""
    if isinstance(value, bool):
        raise InvalidInput("page is a boolean")
    if isinstance(value, int):
        page = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise InvalidInput("page is not a whole number")
        page = int(value)
    elif isinstance(value, str):
        m = _INT_RE.match(value)
        if not m:
            raise InvalidInput("page is not a number")
        page = int(m.group(1))
    else:
        raise InvalidInput("page has the wrong type")
    if page < 1 or page > MAX_PAGES:
        raise InvalidInput("page out of range")
    return page


def parse_percentage(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidInput("percentage has the wrong type")
    if not math.isfinite(value) or value < 0 or value > 1:
        raise InvalidInput("percentage out of range")
    return float(value)


def pages_from_percentage(page: int, percentage: float | None) -> int | None:
    """Fallback page count when the PDF could not be parsed: KOReader's percentage is
    page / pages floored to 4 decimals, so pages ~= page / percentage."""
    if not percentage or percentage <= 0:
        return None
    pages = round(page / percentage)
    return pages if pages >= page else page


def kosync_to_abs(page: int, pages: int | None) -> tuple[dict, bool]:
    """ABS PATCH /api/me/progress/<item> payload for a KOReader page.

    Returns (payload, clamped). isFinished is sent only at the last page; reading back
    from a finished book never un-finishes it (status lives in ABS).
    """
    clamped = False
    if pages is not None and page > pages:
        page, clamped = pages, True
    payload: dict = {"ebookLocation": str(page)}
    if pages:
        payload["ebookProgress"] = (page - 1) / pages
        if page >= pages:
            payload["isFinished"] = True
    return payload, clamped


def abs_to_kosync(progress: dict | None, pages: int | None) -> dict | None:
    """kosync fields (progress, percentage, timestamp) from an ABS media progress, or
    None when ABS has no usable PDF page."""
    if not isinstance(progress, dict):
        return None
    try:
        page = parse_page(progress.get("ebookLocation"))
    except InvalidInput:
        return None
    if pages:
        page = min(page, pages)
        percentage = round_percent(page / pages)
    else:
        raw = progress.get("ebookProgress")
        percentage = round_percent(float(raw)) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0
        percentage = min(max(percentage, 0.0), 1.0)
    last = progress.get("lastUpdate")
    timestamp = int(last // 1000) if isinstance(last, (int, float)) and not isinstance(last, bool) else None
    out = {"progress": str(page), "percentage": percentage}
    if timestamp is not None:
        out["timestamp"] = timestamp
    return out
