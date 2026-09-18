# readwise-koreader

Mirror Readwise Reader articles onto the reMarkable **for KOReader** (plain EPUB files),
so highlights you make land in KOReader `.sdr` sidecars and flow to Obsidian via the
`koreader-highlights` job.

- **Source:** Readwise Reader API v3 `/list/`, locations `LOCATIONS` (new,later,shortlist),
  keeping only docs carrying `TAG` (default `remarkable`).
- **Render:** each article's `html_content` → EPUB via Calibre `ebook-convert`.
- **Deliver:** copy the EPUB into `RM_BOOKS_DIR` (`/home/root/books/Readwise`) over dropbear
  SSH — KOReader reads plain files, not xochitl's store. Reconciles: delivers newly-tagged
  docs, removes ones no longer tagged (only files it created, tracked in
  `/opt/kdk-lab/readwise-koreader/readwise-koreader.json`). Leaves `.sdr` sidecars on removal
  so un-ingested highlights survive.
- **Why not rmfakecloud sync:** that lands in the *native* reader; KOReader needs files.
  ("Use Chaptarr where possible" — Chaptarr can't fetch web articles, so this small fetcher
  handles Readwise, but delivery reuses book-sync's KOReader file-push.)

**Secrets:** `READWISE_TOKEN` (`op://kdk-cluster/Readwise-token-ro`), `RM_PW`
(`op://kdk-ops/remarkable-device`) — op-injected into `.env` by the pre_deploy step.

**Status (2026-09-18):** built, **not yet deployed/live-tested** — needs a run against the
live Readwise API to confirm the v3 tag filter + EPUB render, and confirmation of the
locations/tag to pull. Supersedes the article leg of `readwise-to-remarkable` (which pushed
to the native reader via rmfakecloud); retire that leg once this is validated.
