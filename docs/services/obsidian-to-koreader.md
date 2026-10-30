---
title: Obsidian to reMarkable
host: kdk-dkr-01
stack: obsidian-to-koreader
tier: internal
status: live
storage: yes
secrets: [remarkable-device]
updated: 2026-10-30
tags: [homelab/service, tier/internal, status/live, remarkable, obsidian]
---

# Obsidian to reMarkable (Vault channel)

Renders Obsidian vault notes to PDF and pushes them onto the **reMarkable Paper
Pro** for KOReader, under `/home/root/books/Homelab`. One-shot container on
**kdk-dkr-01**, run periodically by a Komodo procedure. Two modes in one run:

- **Vault channel (request-driven):** the
  [`obsidian-send-to-remarkable`](../../remarkable/obsidian-send-to-remarkable/)
  plugin drops a JSON request into `<vault>/_remarkable/queue/`; the lab mirror
  gets it through Obsidian Sync (no network/token), the job renders the chosen
  **scope** (a note, a folder, or the whole vault) and pushes it, then writes the
  result to `_remarkable/done/`.
- **Scheduled section sync (default, unchanged):** when the queue is empty, every
  run reconciles each top-level vault section to one PDF per section.

**Related:** [[kdk-dkr-01]] · [[calibre]] · [[obsidian-headless]] · remarkable-ocr · koreader-highlights

## At a glance

| Field | Value |
|---|---|
| Purpose | Vault notes → PDF → KOReader on the tablet (outbound leg of the notes loop) |
| URL | — (no ingress; SSH to the tablet, tailnet-first then LAN) |
| Host | [[kdk-dkr-01]] |
| Stack | `stacks/obsidian-to-koreader` (one-shot `obsidian-to-koreader.py`) |
| Image | `kdk/obsidian-to-koreader:latest` (built locally — `auto_pull = false`, `run_build = true`) |
| Renderer | pandoc + **WeasyPrint** (`weasyprint==70.0` + `pydyf==0.12.1`); see the stack README bake-off |
| Data | `/opt/kdk-lab/obsidian-to-koreader` (state + build), vault `_remarkable/` queue |
| Deploy | Komodo procedure `obsidian-to-koreader`, `0 48 */6 * * *` (after docs-to-vault :42) |
| Blast radius | tablet misses new note PDFs; existing PDFs + all return paths unaffected |

## Verify

```bash
# the procedure's last run logs (on kdk-dkr-01)
ssh kdkadmin@10.1.20.20 'docker logs obsidian-to-koreader --tail 20'
# tablet has the Homelab PDFs (when awake); tailnet first, LAN fallback
ssh root@100.103.233.103 'ls -la /home/root/books/Homelab' 2>/dev/null \
  || ssh root@10.1.30.245 'ls -la /home/root/books/Homelab'
```

A plugin "send" is picked up on the next scheduled run; run the Komodo
`obsidian-to-koreader` procedure by hand for an immediate push.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| "tablet asleep; skipping" and request stays in `queue/` | tablet off Wi-Fi/tailnet (expected) | retries next run; `ssh root@10.1.30.245 echo ok` when awake |
| Request in `done/` with `status: failed` | bad scope/path, empty note, or render error | read `done/<id>.json` → `status_detail` |
| Request `status: partial` | some PDFs delivered, others skipped (size guard / render) | `status_detail` lists the failures |
| Image missing in a PDF | embed path unresolved; WeasyPrint warns, does not abort | check the embed exists in the vault; text still renders |
| Dataview block shows a placeholder | Dataview is dynamic, headless-blind (by design) | expected — view the live note in Obsidian |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[obsidian-headless]] vault mirror (`/opt/kdk-lab/obsidian/vault`) | nothing to render |
| Needs | the tablet reachable over SSH (tailnet/LAN) + `RM_PW` | no delivery (requests stay queued) |
| Needed by | the owner reading notes on the tablet in KOReader | — |
| Pairs with | `remarkable-ocr`, `koreader-highlights` (the return loop) | annotations would not come back |

## Observability

No dashboard. Logs ship to Loki via the observability agent; failures page
Pushover (priority 1). Per-request outcomes live in `_remarkable/done/`.

## Architecture

One locally-built image (`python:3.12-slim` + pandoc + WeasyPrint + openssh/sshpass).
`obsidian-to-koreader.py` runs once: process the request queue, else run the
scheduled section reconcile. The vault is mounted **read-only** for content; the
only writable vault path is `_remarkable/` (a nested read-write mount) so a
processed request can move from `queue/` to `done/`. Obsidian syntax is normalised
before pandoc (wikilinks → text, callouts → labelled blockquotes, image embeds →
resolved absolute paths, note embeds dropped, Dataview → placeholder, `%%`
comments stripped). Delivery is SSH/scp, tailnet (`100.103.233.103`) first then LAN
(`10.1.30.245`), opportunistic (the tablet sleeps; probe with SSH, not ICMP).

## The return loop (handwriting + KOReader highlights)

This stack is the **outbound** leg only — it never writes a vault note and there is
**no second return path**. Edits made on the tablet come back through the existing
jobs:

- **Handwriting** (pen annotations on these PDFs, or new notebook pages):
  [`remarkable-ocr`](../../stacks/remarkable-ocr/README.md) pulls changed pages over
  SSH, OCRs them with `qwen2.5vl`, and writes one note per page into the matching
  vault section (`10.Homelab`, …). A `@dia` page becomes a topology SVG. Those notes
  then surface in the daily note by their `[[Source]]`/section links.
- **KOReader highlights** (text highlights on the delivered PDFs):
  [`koreader-highlights`](../../stacks/koreader-highlights/README.md) pulls each
  `.sdr` sidecar under `/home/root/books` and writes one note per book into
  `30.Books/Highlights/`.

Both write into the headless vault and sync to every device; neither overwrites the
source note (append-only), so a note sent to the tablet and later annotated does not
clobber the Obsidian original. The tablet copies are treated as read-plus-annotate.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/obsidian-to-koreader/compose.yaml` | service, RO vault + RW `_remarkable/` mounts, `RM_HOST_TAILNET`/`RM_HOST_LAN`, `PANDOC_ENGINE`, `MAX_PDF_MB` |
| `stacks/obsidian-to-koreader/.env.tpl` | `RM_PW` (op-injected) |
| `/opt/kdk-lab/obsidian/vault/_remarkable/{queue,done}` | the plugin's request channel (synced from the desktop vault) |
| `/opt/kdk-lab/obsidian-to-koreader` | state (`obsidian-to-koreader.json`) + build scratch |
| Komodo procedure `obsidian-to-koreader` | kdk-dkr-01, `0 48 */6 * * *`, `run_build = true` |

## Secret Rotation

`RM_PW` = `op://kdk-cluster/remarkable-device/password` (the tablet root
password), op-injected into `.env` by the Komodo `pre_deploy` step. Rotate per
[rotate-a-secret](../runbooks/rotate-a-secret.md); no other secret.

## User Guide

Install the **Send to reMarkable** plugin (see
`remarkable/obsidian-send-to-remarkable/README.md`), then use its ribbon button or
command on a note, right-click a folder → *Send folder to reMarkable*, or
*Send whole vault*. The request appears on the tablet after the next run (or run the
Komodo procedure for an immediate push). Read the PDFs in KOReader under
`books/Homelab`. Annotate freely — handwriting and highlights return to the vault
via the jobs above.
