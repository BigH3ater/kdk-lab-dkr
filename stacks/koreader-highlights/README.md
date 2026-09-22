# koreader-highlights

Ingest KOReader highlights from the reMarkable into the Obsidian vault.

- **Source:** KOReader writes annotations to a `.sdr` sidecar next to each book
  (`<book>.sdr/metadata.<ext>.lua`). This pulls every sidecar under `RM_BOOKS_ROOT`
  (`/home/root/books`, i.e. both `Library/` ebooks and `Readwise/` articles) over dropbear SSH.
- **Parse:** `slpp` decodes the Lua table → `doc_props` (title/author) + `annotations`
  (text, note, chapter, page, datetime). Handles the older per-page `highlight` format too.
- **Write:** one Markdown note per book at `HL_SUBDIR` (`30.Books/Highlights/<Title>.md`) in the
  headless-synced vault, so it propagates to all devices. Idempotent — a note is rewritten only
  when its sidecar's mtime changed (tracked in `/opt/kdk-lab/koreader-highlights`).

Opportunistic (tablet-awake); Pushover on failure. This is the ingest leg of the KOReader
reading loop — reading + highlighting happen on-device in KOReader; both `book-sync` (ebooks)
and `readwise-koreader` (articles) feed KOReader, and their highlights land here.

**Secret:** `RM_PW` (`op://kdk-ops/remarkable-device/password`), op-injected into `.env`.
Also available as a client-side alternative: the KOHi / KOReader Highlight Importer Obsidian plugins.
