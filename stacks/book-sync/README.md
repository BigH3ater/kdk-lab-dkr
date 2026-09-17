# book-sync

Mirrors **Chaptarr's ebook library** onto the reMarkable's managed **"Kodiak Library"**
folder. Chaptarr (fed by Prowlarr + SABnzbd) is the book manager; book-sync reflects it
to the tablet. Add a book in Chaptarr → it appears on the tablet; remove it → it's removed
("send/remove at will"). Replaces the retired one-way `ebooks` leg of
`readwise-to-remarkable` (which only ever pushed, via rmfakecloud).

## Reconcile (`book-sync.py`, run by a Komodo procedure)

- in the library (`/mnt/media/books`), not on tablet (or its md5 changed) → push / replace.
- managed on the tablet, gone from the library → remove it.
- **Only documents this service created are ever deleted** — their UUIDs live in
  `/opt/kdk-lab/book-sync/book-sync.json`. Books you add on the tablet directly are
  never in that state and never touched.

Direct dropbear SSH into xochitl (reliable here; rmapi/rmfakecloud is the retired path).
Opportunistic: tablet asleep → this run no-ops, the next reconciles. `xochitl` restarts
only when something changed. Pages Pushover on failure. Recursive, so Chaptarr's
`Author/Title/book.epub` layout is fine.

## Config (`.env`, op-injected)

`RM_HOST` / `RM_PW` (`op://kdk-ops/remarkable-device`), `RM_LIB_FOLDER`. Source is
`BOOKS_DIR` (default `/books`, bind-mounted from `/mnt/media/books`).

## Related

Acquisition pipeline: **Prowlarr** (indexers) → **Chaptarr** (`/data/media/books`) ←
**SABnzbd** (`/data/media/staging`), all sharing `/mnt/media:/data/media` so imports
hardlink and no download-client path mapping is needed.
