"""The few Audiobookshelf API calls the helper makes, as the jmack API key.

Endpoints checked in the ABS 2.37.2 source (server/routers/ApiRouter.js):
  GET   /api/libraries/:id/items?minified=1   items; id, updatedAt, media.ebookFormat on each
  GET   /api/items/:id                        libraryId, mediaType, media.ebookFile
                                              (ebookFormat, metadata.path/size), media.audioFiles
  GET   /api/me/progress/:libraryItemId       404 when there is no progress
  PATCH /api/me/progress/:libraryItemId       MeController.createUpdateMediaProgress
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from .config import UUID_RE


EBOOK_FORMATS = ("pdf", "epub")


class AbsError(Exception):
    pass


def _item_path(item_id: str) -> str:
    if not UUID_RE.match(item_id):
        raise AbsError("bad item id")
    return quote(item_id, safe="-")


class AbsClient:
    def __init__(self, base_url: str, api_key: str, timeout: float, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            transport=transport,
        )
        # item id -> (updatedAt, minified format, (path, format) or None): the ebookFile
        # detail of each item, re-read only when ABS reports the item changed.
        self._ebook_cache: dict[str, tuple[str, str, tuple[str, str] | None]] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict | None = None) -> dict | None:
        r = await self._client.get(path, params=params)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise AbsError(f"GET status {r.status_code}")
        data = r.json()
        if not isinstance(data, dict):
            raise AbsError("unexpected JSON")
        return data

    async def list_ebook_items(self, library_id: str) -> list[tuple[str, str, str]]:
        """(item_id, ebook path as ABS sees it, format) for every item whose PRIMARY
        ebook is a PDF or an EPUB.

        Supplementary files are skipped on purpose: ABS keeps one ebook position per
        item, and it belongs to the primary ebook (a page for a PDF, a CFI for an EPUB).

        One GET per item only for new or changed items: the detail is cached by
        (item id, updatedAt) from the minified list; an item without updatedAt is always
        re-read. Any error raises (the caller keeps its last good map); the cache only
        ever holds answers ABS gave.
        """
        lib = _item_path(library_id)
        data = await self._get_json(f"/api/libraries/{lib}/items", params={"minified": "1"})
        if data is None:
            raise AbsError("library not found")
        out: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for item in data.get("results") or []:
            media = (item or {}).get("media") or {}
            fmt = media.get("ebookFormat")
            if fmt not in EBOOK_FORMATS:
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not UUID_RE.match(item_id):
                continue
            seen.add(item_id)
            updated = item.get("updatedAt")
            stamp = str(updated) if updated is not None else None
            cached = self._ebook_cache.get(item_id)
            if stamp is not None and cached is not None and cached[:2] == (stamp, fmt):
                found = cached[2]
            else:
                full = await self._get_json(f"/api/items/{_item_path(item_id)}")
                ebook = ((full or {}).get("media") or {}).get("ebookFile") or {}
                path = (ebook.get("metadata") or {}).get("path")
                found = (path, fmt) if ebook.get("ebookFormat") == fmt and isinstance(path, str) else None
                if full is None or stamp is None:
                    self._ebook_cache.pop(item_id, None)
                else:
                    self._ebook_cache[item_id] = (stamp, fmt, found)
            if found is not None:
                out.append((item_id, found[0], found[1]))
        for gone in set(self._ebook_cache) - seen:
            del self._ebook_cache[gone]
        return out

    async def list_pdf_items(self, library_id: str) -> list[tuple[str, str]]:
        """(item_id, path) for every item whose primary ebook is a PDF."""
        return [(i, p) for i, p, fmt in await self.list_ebook_items(library_id) if fmt == "pdf"]

    async def get_item(self, item_id: str) -> dict | None:
        """The full library item (GET /api/items/:id), None when ABS has no such item.

        Every other failure is an AbsError (a transport error, a status, a body that is
        not JSON), so the EPUB link logs it as abs_error, not as an internal error. The
        PDF path keeps the httpx errors (a timeout there is a 504 to the tablet)."""
        try:
            return await self._get_json(f"/api/items/{_item_path(item_id)}")
        except httpx.HTTPError as e:
            raise AbsError(f"GET {type(e).__name__}") from None
        except ValueError:
            raise AbsError("bad JSON") from None

    async def get_progress(self, item_id: str) -> dict | None:
        return await self._get_json(f"/api/me/progress/{_item_path(item_id)}")

    async def patch_progress(self, item_id: str, payload: dict) -> None:
        r = await self._client.patch(f"/api/me/progress/{_item_path(item_id)}", json=payload)
        if r.status_code != 200:
            raise AbsError(f"PATCH status {r.status_code}")
