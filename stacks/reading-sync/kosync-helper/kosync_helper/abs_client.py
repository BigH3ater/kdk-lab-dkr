"""The few Audiobookshelf API calls the helper makes, as the jmack API key.

Endpoints checked in the ABS 2.37.2 source (server/routers/ApiRouter.js):
  GET   /api/libraries/:id/items?minified=1   items; media.ebookFormat on each
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
        """
        lib = _item_path(library_id)
        data = await self._get_json(f"/api/libraries/{lib}/items", params={"minified": "1"})
        if data is None:
            raise AbsError("library not found")
        out: list[tuple[str, str, str]] = []
        for item in data.get("results") or []:
            media = (item or {}).get("media") or {}
            fmt = media.get("ebookFormat")
            if fmt not in EBOOK_FORMATS:
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not UUID_RE.match(item_id):
                continue
            full = await self._get_json(f"/api/items/{_item_path(item_id)}")
            ebook = ((full or {}).get("media") or {}).get("ebookFile") or {}
            path = (ebook.get("metadata") or {}).get("path")
            if ebook.get("ebookFormat") == fmt and isinstance(path, str):
                out.append((item_id, path, fmt))
        return out

    async def list_pdf_items(self, library_id: str) -> list[tuple[str, str]]:
        """(item_id, path) for every item whose primary ebook is a PDF."""
        return [(i, p) for i, p, fmt in await self.list_ebook_items(library_id) if fmt == "pdf"]

    async def get_item(self, item_id: str) -> dict | None:
        """The full library item (GET /api/items/:id), None when ABS has no such item."""
        return await self._get_json(f"/api/items/{_item_path(item_id)}")

    async def get_progress(self, item_id: str) -> dict | None:
        return await self._get_json(f"/api/me/progress/{_item_path(item_id)}")

    async def patch_progress(self, item_id: str, payload: dict) -> None:
        r = await self._client.patch(f"/api/me/progress/{_item_path(item_id)}", json=payload)
        if r.status_code != 200:
            raise AbsError(f"PATCH status {r.status_code}")
