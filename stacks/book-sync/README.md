# book-sync

Keeps the reMarkable's managed **"Kodiak Library"** folder in sync with the vault's
**`30-Books/ToTablet/`** (EPUB/PDF). Source of truth is the folder — drop a book in
(you, or Chaptarr) and it lands on the tablet; delete it and it's removed from the
tablet ("remove at will").

## Reconcile (`book-sync.py`, run by a Komodo procedure)

- in folder, not on tablet (or its md5 changed) → push / replace the doc.
- managed on the tablet, gone from the folder → remove it.
- **Only documents this service created are ever deleted** — their UUIDs live in
  `/opt/kdk-lab/book-sync/book-sync.json`. Books you add on the tablet directly are
  never in that state and never touched.

Direct dropbear SSH into xochitl (reliable here; rmapi would need the cloud).
Opportunistic: tablet asleep → this run no-ops, the next reconciles. `xochitl`
restarts only when something changed. Pages Pushover on failure.

## Config (`.env`, op-injected)

`RM_HOST` / `RM_PW` (`op://kdk-ops/remarkable-device`), `BOOKS_SUBDIR`, `RM_LIB_FOLDER`.
