# reading-sync

abs-opds (OPDS catalogue of the ABS `ebooks` library), kosync-helper (KOReader Progress
sync: PDFs page-for-page to Audiobookshelf, everything else to BookBridge) and BookBridge
(EPUB position to ABS CFI), on kdk-dkr-dmz-01. LAN + tailnet only.

- Service page: [docs/services/reading-sync.md](../../docs/services/reading-sync.md)
- Helper tests: `cd kosync-helper && uv run --frozen pytest -q`
- EPUB auto-link one-time setup (1Password item + kdkadmin password): doc page, EPUB auto-link
- Baseline: `python3 scripts/compose-baseline-check.py --stack reading-sync`
