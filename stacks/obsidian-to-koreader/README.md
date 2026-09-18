# obsidian-to-koreader

Render each Obsidian vault **section** to a PDF and put it on the reMarkable for KOReader —
the "read your notes/docs on the tablet" loop.

- **Source:** the headless-synced vault (`/opt/kdk-lab/obsidian/vault`, mounted **read-only**).
  For each top-level section matching `SECTION_REGEX` (default numbered domains `00-Inbox`,
  `10-Homelab`, `20-Personal`, `30-Books`, `40-Development`, `50-Learning`) it concatenates
  the section's Markdown (frontmatter stripped, `[[wikilinks]]`→text, embeds dropped).
- **Render:** `pandoc --pdf-engine=weasyprint` → one A5 PDF per section.
- **Deliver:** copy `\<section\>.pdf` into `RM_DEST` (`/home/root/books/Homelab`) over dropbear SSH.
  Reconciles by content hash (rebuild changed sections, remove PDFs for deleted sections). A
  `MAX_PDF_MB` guard (40MB) skips a section that would render too large for the tablet.

Read-only on the vault (never writes back). Opportunistic (tablet-awake); Pushover on failure.

**Secret:** `RM_PW` (`op://kdk-ops/remarkable-device/password`), op-injected into `.env`.
