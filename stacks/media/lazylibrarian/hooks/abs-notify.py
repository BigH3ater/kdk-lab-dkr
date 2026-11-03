#!/usr/bin/env python3
# ruff: noqa
"""
LazyLibrarian -> Audiobookshelf post-import hook.

Wired to LazyLibrarian's *Custom* notifier (Config -> Notifications -> Custom,
"Run on download"). LazyLibrarian's custom notifier executes this script after a
book is imported, passing the matching `books` table row (plus the `wanted` row)
as a flat list of KEY VALUE pairs on argv -- verified in
lazylibrarian/notifiers/custom_notify.py (CustomNotifier._notify ->
common.run_script, which Popen()s the script directly, so this file needs a
shebang and the executable bit).

It does two things, for AudioBook imports only (AuxInfo == "AudioBook"):

  1. Writes a Calibre-style `metadata.opf` sidecar into the audiobook's folder
     with <meta name="calibre:series"> and <meta name="calibre:series_index">.
     LazyLibrarian itself only writes OPF files for *ebooks* (postprocess.py
     returns before create_opf() for non-ebooks, and the writeOPF API keys on
     BookFile), so without this hook an audiobook folder never gets an OPF and
     Audiobookshelf can only learn the series *name* from the folder path, never
     the number. Audiobookshelf's metadataPrecedence puts opfFile above
     folderStructure, so this sidecar gives ABS the series name AND index.

  2. Triggers an Audiobookshelf library scan (POST /api/libraries/{id}/scan).
     ABS reads the audiobook library read-only over NFS from a different host and
     has no inotify across the mount, so a new import is invisible until a scan
     is requested.

Everything is best-effort: the scan is always attempted even if the OPF step
fails, and the script always exits 0 so a hook error never blocks LazyLibrarian.
Standard library only (no requests); urllib is used so the stock image needs no
extra packages.

Environment (set on the lazylibrarian service in stacks/media/compose.yaml):
  ABS_URL          base URL of Audiobookshelf, e.g. https://audiobookshelf.kmkdp.com
  ABS_LIBRARY_ID   the audiobook library id
  ABS_API_TOKEN    ABS admin API token (SECRET; from /etc/kdk/abs-notify.env, op-read)
Optional:
  ABS_NOTIFY_DEBUG=1   extra logging to stdout (captured in LL's dlcomms debug log)
"""

import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.sax.saxutils as sx


def log(msg: str) -> None:
    print(f"[abs-notify] {msg}", flush=True)


def argv_to_dict(argv: list) -> dict:
    """LazyLibrarian passes the DB row as KEY VALUE KEY VALUE ... on argv."""
    out = {}
    it = iter(argv)
    for key in it:
        try:
            out[key] = next(it)
        except StopIteration:
            out[key] = ""
    return out


def parse_series(series_display: str):
    """
    Parse LazyLibrarian's SeriesDisplay (built from the configured Series
    Pattern) into (name, index). SeriesDisplay holds the formatted series, may
    list several series separated by '<br>' -- take the first -- and typically
    looks like 'Expeditionary Force #6'. Degrade gracefully: if no number is
    found, return (name, "") so ABS still gets the series name.
    """
    if not series_display:
        return ("", "")
    first = series_display.split("<br>")[0].strip()
    # LazyLibrarian passes a missing value as the literal string "None"
    # (str(None)); seen on A Game of Thrones, 2026-11-02.
    if not first or first.lower() in ("none", "null"):
        return ("", "")
    # '... #6', '... # 6', '... Book 6', or a trailing number.
    m = re.search(r"^(.*?)[\s,]*(?:#|book\s*)\s*([0-9]+(?:\.[0-9]+)?)\s*$", first, re.I)
    if m:
        return (m.group(1).strip(" .-#"), m.group(2))
    m = re.search(r"^(.*?)[\s,]+([0-9]+(?:\.[0-9]+)?)\s*$", first)
    if m:
        return (m.group(1).strip(" .-#"), m.group(2))
    return (first, "")


def series_from_db(book_id: str):
    """
    SeriesDisplay is often still empty at import time even when LazyLibrarian
    knows the series: Hardcover-sourced books get member/series rows straight
    away (seen 2026-11-02: Columbus Day = member HC1086 #1, SeriesDisplay NULL).
    Read the first series for this BookID from LazyLibrarian's own database,
    read-only. LL_DB overrides the path.
    """
    if not book_id:
        return ("", "")
    db = os.environ.get("LL_DB", "/config/lazylibrarian.db")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute(
                "SELECT s.SeriesName, m.SeriesNum FROM member m "
                "JOIN series s ON s.SeriesID = m.SeriesID "
                "WHERE m.BookID = ? ORDER BY m.rowid LIMIT 1",
                (book_id,),
            ).fetchone()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        log(f"series lookup in {db} failed: {type(e).__name__}: {e}")
        return ("", "")
    if not row or not row[0]:
        return ("", "")
    name = str(row[0]).strip()
    num = "" if row[1] in (None, "", "None") else str(row[1]).strip()
    return ("", "") if name.lower() in ("none", "null") else (name, num)


def build_opf(title: str, author: str, series: str, index: str) -> str:
    def esc(v: str) -> str:
        return sx.escape(v or "")

    meta = []
    if series:
        meta.append(f'    <meta name="calibre:series" content="{esc(series)}"/>')
    if series and index:
        meta.append(f'    <meta name="calibre:series_index" content="{esc(index)}"/>')
    meta_block = ("\n" + "\n".join(meta)) if meta else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uuid_id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">\n'
        f"    <dc:title>{esc(title)}</dc:title>\n"
        f'    <dc:creator opf:role="aut">{esc(author)}</dc:creator>'
        f"{meta_block}\n"
        "  </metadata>\n"
        "</package>\n"
    )


def write_opf(folder: str, title: str, author: str, series: str, index: str) -> None:
    if not series:
        log("no series on this book; leaving folder structure to carry it, no OPF written")
        return
    if not folder or not os.path.isdir(folder):
        log(f"audiobook folder not found, skipping OPF: {folder!r}")
        return
    path = os.path.join(folder, "metadata.opf")
    tmp = path + ".tmp"
    data = build_opf(title, author, series, index)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, path)
    log(f"wrote {path} (series={series!r} index={index or '-'})")


def trigger_scan() -> None:
    base = (os.environ.get("ABS_URL") or "").rstrip("/")
    lib = os.environ.get("ABS_LIBRARY_ID") or ""
    token = os.environ.get("ABS_API_TOKEN") or ""
    if not (base and lib and token):
        log("ABS_URL / ABS_LIBRARY_ID / ABS_API_TOKEN not all set; skipping scan")
        return
    url = f"{base}/api/libraries/{lib}/scan"
    req = urllib.request.Request(url, data=b"{}", method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                log(f"ABS scan requested: HTTP {resp.status}")
                return
        except urllib.error.HTTPError as e:
            log(f"ABS scan HTTP error {e.code} (attempt {attempt})")
            if e.code in (401, 403, 404):
                return  # not retryable
        except Exception as e:  # noqa: BLE001
            log(f"ABS scan error (attempt {attempt}): {type(e).__name__}: {e}")
        time.sleep(2 * attempt)
    log("ABS scan failed after retries")


def main() -> int:
    data = argv_to_dict(sys.argv[1:])
    if os.environ.get("ABS_NOTIFY_DEBUG") == "1":
        log("argv keys: " + ", ".join(sorted(data)))
    aux = (data.get("AuxInfo") or "").strip().lower()
    if aux != "audiobook":
        log(f"not an audiobook (AuxInfo={data.get('AuxInfo')!r}); nothing to do")
        return 0

    audio_file = data.get("AudioFile") or ""
    folder = os.path.dirname(audio_file) if audio_file else ""
    title = data.get("BookName") or ""
    # The books row has no AuthorName (AuthorID only); fall back to the
    # library layout Author/[Series/]Title -> first path segment.
    author = data.get("AuthorName") or ""
    if not author and folder:
        rel = os.path.relpath(folder, os.environ.get("ABS_AUDIO_ROOT", "/data/media/books/audiobook"))
        if not rel.startswith(".."):
            author = rel.split(os.sep)[0]
    series, index = parse_series(data.get("SeriesDisplay") or "")
    if not series:
        series, index = series_from_db(data.get("BookID") or "")

    try:
        write_opf(folder, title, author, series, index)
    except Exception as e:  # noqa: BLE001
        log(f"OPF write failed (continuing to scan): {type(e).__name__}: {e}")

    trigger_scan()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001
        log(f"unexpected error: {type(e).__name__}: {e}")
        sys.exit(0)  # never block LazyLibrarian
