"""The few Audiobookshelf API calls the helper makes, as the jmack API key.

Endpoints checked in the ABS 2.37.2 source (server/routers/ApiRouter.js):
  GET   /api/libraries/:id/items?minified=1   items; media.ebookFormat on each
  GET   /api/items/:id                        media.ebookFile.metadata.path
  GET   /api/me/progress/:libraryItemId       404 when there is no progress
  PATCH /api/me/progress/:libraryItemId       MeController.createUpdateMediaProgress
"""
from __future__ import annotations

from urllib.parse import quote

import httpx

from .config import UUID_RE


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

    async def list_pdf_items(self, library_id: str) -> list[tuple[str, str]]:
        """(item_id, ebook path as ABS sees it) for every item whose PRIMARY ebook is a PDF.

        Supplementary PDFs are skipped on purpose: ABS keeps one ebook position per
        item, and for an item whose primary ebook is an EPUB that position is a CFI.
        """
        lib = _item_path(library_id)
        data = await self._get_json(f"/api/libraries/{lib}/items", params={"minified": "1"})
        if data is None:
            raise AbsError("library not found")
        out: list[tuple[str, str]] = []
        for item in data.get("results") or []:
            media = (item or {}).get("media") or {}
            if media.get("ebookFormat") != "pdf":
                continue
            item_id = item.get("id")
            if not isinstance(item_id, str) or not UUID_RE.match(item_id):
                continue
            full = await self._get_json(f"/api/items/{_item_path(item_id)}")
            ebook = ((full or {}).get("media") or {}).get("ebookFile") or {}
            path = (ebook.get("metadata") or {}).get("path")
            if ebook.get("ebookFormat") == "pdf" and isinstance(path, str):
                out.append((item_id, path))
        return out

    async def get_progress(self, item_id: str) -> dict | None:
        return await self._get_json(f"/api/me/progress/{_item_path(item_id)}")

    async def patch_progress(self, item_id: str, payload: dict) -> None:
        r = await self._client.patch(f"/api/me/progress/{_item_path(item_id)}", json=payload)
        if r.status_code != 200:
            raise AbsError(f"PATCH status {r.status_code}")
