---
title: Add a book to LazyLibrarian
---

# Add a book to LazyLibrarian

How to get one ebook into the library when Libreseerr or a LazyLibrarian search
does not do it on its own: a file you already have, a book LazyLibrarian has no
record of, or a book no metadata source knows. Written from the 2026-11-08
session (vault: `4. Learning/Reviews/Books Review 2026-11-08.md`).

**Related:** [LazyLibrarian](../services/lazylibrarian.md) · [Calibre](../services/calibre.md) · [Rotate a secret](rotate-a-secret.md)

Try the paths **in order**. Each one is cheaper than the next.

| Path | When | Ends in |
|---|---|---|
| [A. Staging import](#a-staging-import) | you have the file, and LazyLibrarian has (or can find) the record | LazyLibrarian renames it into the library, Calibre imports it |
| [B. Add the record](#b-add-the-record-first) | LazyLibrarian has no record and `findBook` finds nothing | a record, then path A |
| [C. Library tree by hand](#c-library-tree-by-hand) | no metadata source has the book at all | Calibre imports it; LazyLibrarian does not track it |

## Setup (laptop)

The API key is read from 1Password on the laptop and never printed. The API is
reached through Traefik's `/api` bypass (LAN or tailnet).

```bash
LL_API_KEY=$(op read 'op://kdk-ops/lazylibrarian-api-rw/credential')
ll() { curl -sG https://lazylibrarian.kmkdp.com/api --data-urlencode "apikey=$LL_API_KEY" "$@"; echo; }
ll --data-urlencode cmd=getVersion                                     # a version string = key and route work
ll --data-urlencode cmd=readCFG --data-urlencode name=book_api --data-urlencode group=API   # [HardCover]
```

Never run `cmd=getHistory` or `cmd=getSnatched` into a terminal you will paste
from: see [The Prowlarr key in download URLs](#the-prowlarr-key-in-download-urls).

## A. Staging import

1. **One book per subfolder** under `/mnt/media/staging/ebook` (the
   `alternate_dir`, `/data/media/staging/ebook` inside the container). On
   kdk-dkr-01 `kdkadmin` is uid 1000, the container's user, so no sudo:

   ```bash
   ssh kdkadmin@10.1.20.20
   mkdir -p /mnt/media/staging/ebook/release-it-2e
   mv ~/Downloads/release-it-2e.epub /mnt/media/staging/ebook/release-it-2e/
   ```

   Several formats of the **same** book (`x.epub` + `x.pdf`) may share a folder.
   Two files of the **same type** may not: LazyLibrarian rejects the folder as a
   multi-book folder (`multibook()`). The API returns `False`, the GUI import
   skips it, and the only trace is a debug-level line
   `Not processing <dir>, found multiple epub`. Split the folder.

2. **The record exists** (it was requested, or you added it in path B): import
   by BookID. Get the id from the book's page in the UI (`…/bookPage?bookid=`)
   or from `getAllBooks`:

   ```bash
   ll --data-urlencode cmd=getAllBooks | python3 -c 'import json,sys; [print(b["BookID"], b["BookName"]) for b in json.load(sys.stdin) if "Release" in b["BookName"]]'
   ll --data-urlencode cmd=importBook --data-urlencode id=<BookID> \
      --data-urlencode dir=/data/media/staging/ebook/release-it-2e     # True = moved into the library
   ```

   `dir` is the **container** path. A BookID not yet in the database is looked up
   in the `book_api` source first, so this also fails while that source is
   [blocked](#metadata-source-blocked).

3. **Or let LazyLibrarian match it**: UI → Manage Books → **Import eBooks** (or
   `ll --data-urlencode cmd=importAlternate --data-urlencode wait=1`). It reads
   the file's own metadata (OPF/EPUB) and matches it against the metadata
   source. A file whose metadata matches nothing is left in staging; use step 2.
   The button does nothing when `alternate_dir` is empty (it was until
   2026-11-08; it is now [pinned](../services/lazylibrarian.md#pinned-settings)).

4. **Verify**: the file is now under
   `/mnt/media/books/ebook/<Author>/<Title>/`, and within 15 minutes Calibre has
   it (`ssh kdkadmin@10.1.20.20 'sudo docker logs calibre-import 2>&1 | grep "<title words>"'`).

## B. Add the record first

1. **By ISBN** (uses only the `book_api` source):

   ```bash
   ll --data-urlencode cmd=addBookByISBN --data-urlencode isbn=9780135957059   # Added …:<author>:<title>
   ```

   `No results for <isbn>` means the source has no such ISBN, **or** the source
   is blocked. Check the [block](#metadata-source-blocked) before you conclude
   the former.

2. **By OpenLibrary work id.** Find the work, then add it naming the source.
   The `source` value is the full source name, case-sensitive: `OpenLibrary`.
   `source=ol` is refused with `Invalid source [HardCover]`; the message prints
   the default source, not the value you sent.

   ```bash
   ll --data-urlencode cmd=findBook --data-urlencode "name=Release It Nygard" --data-urlencode source=OpenLibrary
   ll --data-urlencode cmd=addBook --data-urlencode id=<OL…W> --data-urlencode source=OpenLibrary --data-urlencode wait=1
   ```

   If `addBook … source=OpenLibrary` is refused, flip the default source for the
   one add, then flip it back (LazyLibrarian saves `config.ini` itself; no
   restart):

   ```bash
   ll --data-urlencode cmd=writeCFG --data-urlencode name=book_api --data-urlencode group=API --data-urlencode value=OpenLibrary
   ll --data-urlencode cmd=addBook --data-urlencode id=<OL…W> --data-urlencode wait=1
   ll --data-urlencode cmd=writeCFG --data-urlencode name=book_api --data-urlencode group=API --data-urlencode value=HardCover
   ll --data-urlencode cmd=readCFG --data-urlencode name=book_api --data-urlencode group=API    # [HardCover]
   ```

3. **Editions.** OpenLibrary files every edition under one *work*: the 2019
   Pragmatic Programmer is an edition of the 1999 work, and the DDIA 2nd-edition
   ISBNs were not in OpenLibrary at all on 2026-11-08. A work-id record carries
   the work's (first-edition) title, so a search can grab the old edition. For a
   new edition, edit the record in the UI (title with the edition, ISBN, date),
   then **lock** it so a refresh does not undo it.

4. Then import the file with [path A](#a-staging-import) step 2, or mark it
   Wanted (`cmd=queueBook&id=<BookID>&type=eBook`) and let the search find it.

## C. Library tree by hand

For a book no source knows (no record can exist). Calibre reads the library
tree directly; LazyLibrarian will not track the book.

```bash
ssh kdkadmin@10.1.20.20
mkdir -p "/mnt/media/books/ebook/Jane Author/Some Title"
mv /mnt/media/staging/ebook/some-title/some-title.epub \
   "/mnt/media/books/ebook/Jane Author/Some Title/Some Title - Jane Author.epub"
```

The layout `Author/Title/Title - Author.ext` matches what LazyLibrarian writes.
`mv` within `/mnt/media` is a rename, not a copy. Within 15 minutes
`calibre-import` adds it (`sudo docker logs calibre-import 2>&1 | grep "Some Title"`).
Calibre still de-duplicates by title and author: a file whose embedded title
equals a book already in Calibre (a 2nd edition titled like the 1st) is skipped,
so give it a distinct title first.

## Metadata source blocked

The lab's `book_api` is **HardCover**. HardCover blocks itself in LazyLibrarian
for **1 day** on any HTTP 400/401/403 and 2 hours on a 500 (`hc.py`). On
2026-11-08 09:12 an `importBook` of a file whose metadata had no match sent a
query HardCover rejected (`invalid_query`); on 2026-11-02 an expired token did
the same (`invalid_token`). `multi_source = True` does not help: it only widens
author refreshes and library scans, not the API search and add calls.

**Symptoms while blocked:** every `findBook` and `addBookByISBN` returns
`No results`, `importBook` cannot add a missing BookID, Libreseerr searches come
back empty. Nothing is logged at error level.

**Alert:** `LazyLibrarianProviderBlocked` (warning, `stacks/loki/rules/media.yml`)
fires on the log line `Blocking provider <name> for <N> days|hours` for a
metadata source. Indexer blocks (30 s, same log line) are not matched.

**Confirm:**

```bash
ssh kdkadmin@10.1.20.20 'sudo docker logs --since 24h media-lazylibrarian-1 2>&1 | grep "Blocking provider" | grep -E "HardCover|OpenLibrary|GoodReads"'
```

or UI → Config → **Blocked Providers** (lists each block, its reason and when it ends).

**Work around it:** the `source=OpenLibrary` calls in [path B](#b-add-the-record-first),
or the `writeCFG book_api` flip for the duration (flip it back afterwards).

**End it early:** UI → Config → Blocked Providers → **Clear** (clears every
blocked provider, indexers included). A restart does **not** clear it: blocks
persist in `/opt/kdk-lab/media/lazylibrarian/blocked_providers.json`. Clear only
after the cause is gone: re-running the same no-match import sends the same bad
query and blocks HardCover for another day, so add that book with path B or C
instead. On `invalid_token`, replace the HardCover token (UI → Config, HardCover API section) and store it in 1Password first.

## The Prowlarr key in download URLs

LazyLibrarian stores each snatch's download URL in the `wanted` table (the UI's
**History**; magazines use `pastissues`), and the indexers that Prowlarr syncs
into LazyLibrarian put **Prowlarr's API key** in that URL as plain text. So:

- Never export, paste, screenshot or attach those rows, the History page, or the
  output of `cmd=getHistory` / `cmd=getSnatched` (both return `SELECT *`,
  `NZBurl` included). Libreseerr calls `getSnatched`, so the key also crosses to
  the DMZ in that reply.
- A read-only DB query names its columns (`BookID, NZBtitle, NZBprov, NZBdate,
  Status`) and never selects `NZBurl` or `*`.
- If the key has been exposed, rotate it per [Rotate a secret](rotate-a-secret.md):
  Prowlarr → Settings → General → API Key → regenerate, store it in 1Password
  in the same step, then **Sync App Indexers** in Prowlarr so LazyLibrarian,
  Sonarr and Radarr get the new key, and update the other consumers listed in
  [media](../services/media.md) (arr API keys row). Old rows keep the dead key.
  This runbook does not rotate it; the open follow-up is in the vault
  (`10.Homelab/Followups/homelab-followups.README.md`).
