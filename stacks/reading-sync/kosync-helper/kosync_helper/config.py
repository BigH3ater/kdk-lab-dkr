"""Settings from the environment (compose `environment` + the op-injected .env)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class Settings:
    abs_url: str
    abs_api_key: str = field(repr=False)
    abs_library_id: str
    bookbridge_url: str
    kosync_user: str
    ebooks_root: str = "/ebooks"
    refresh_seconds: int = 3600
    auth_cache_seconds: int = 300
    upstream_timeout: float = 4.0
    max_body_bytes: int = 64 * 1024
    # EPUB auto-link (BookBridge web UI, session login). Off while user or password is empty.
    bookbridge_web_url: str = "http://bookbridge:5757"
    bookbridge_web_user: str = ""
    bookbridge_web_password: str = field(default="", repr=False)
    epub_link_delay_seconds: float = 10.0

    @property
    def epub_autolink_enabled(self) -> bool:
        return bool(self.bookbridge_web_user and self.bookbridge_web_password)

    def __post_init__(self) -> None:
        if not self.abs_api_key:
            raise ValueError("ABS_API_KEY is empty")
        if not UUID_RE.match(self.abs_library_id):
            raise ValueError("ABS_LIBRARY_ID is not a UUID")
        if not self.kosync_user:
            raise ValueError("KOSYNC_USER is empty")
        for name in ("abs_url", "bookbridge_url", "bookbridge_web_url"):
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
        bookbridge_web_url=e.get("BOOKBRIDGE_WEB_URL", "http://bookbridge:5757").rstrip("/"),
        bookbridge_web_user=e.get("BOOKBRIDGE_WEB_USER", ""),
        bookbridge_web_password=e.get("BOOKBRIDGE_WEB_PASSWORD", ""),
        epub_link_delay_seconds=float(e.get("EPUB_LINK_DELAY_SECONDS", "10")),
    )
