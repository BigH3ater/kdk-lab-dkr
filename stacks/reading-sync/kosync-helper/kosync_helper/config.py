"""Settings from the environment (compose `environment` + the op-injected .env)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class Settings:
    abs_url: str
    abs_api_key: str
    abs_library_id: str
    bookbridge_url: str
    kosync_user: str
    ebooks_root: str = "/ebooks"
    refresh_seconds: int = 3600
    auth_cache_seconds: int = 300
    upstream_timeout: float = 4.0
    max_body_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if not self.abs_api_key:
            raise ValueError("ABS_API_KEY is empty")
        if not UUID_RE.match(self.abs_library_id):
            raise ValueError("ABS_LIBRARY_ID is not a UUID")
        if not self.kosync_user:
            raise ValueError("KOSYNC_USER is empty")
        for name in ("abs_url", "bookbridge_url"):
            if not getattr(self, name).startswith(("http://", "https://")):
                raise ValueError(f"{name.upper()} must be an http(s) URL")


def from_env() -> Settings:
    e = os.environ
    return Settings(
        abs_url=e.get("ABS_URL", "http://audiobookshelf/audiobookshelf").rstrip("/"),
        abs_api_key=e.get("ABS_API_KEY", ""),
        abs_library_id=e.get("ABS_LIBRARY_ID", ""),
        bookbridge_url=e.get("BOOKBRIDGE_KOSYNC_URL", "http://bookbridge:5758").rstrip("/"),
        kosync_user=e.get("KOSYNC_USER", ""),
        ebooks_root=e.get("EBOOKS_ROOT", "/ebooks").rstrip("/") or "/",
        refresh_seconds=int(e.get("PDF_MAP_REFRESH_SECONDS", "3600")),
        auth_cache_seconds=int(e.get("AUTH_CACHE_SECONDS", "300")),
        upstream_timeout=float(e.get("UPSTREAM_TIMEOUT_SECONDS", "4")),
    )
