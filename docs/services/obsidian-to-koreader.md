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
Pro** for KOReader, under `/home/root/books/kodiak-codex` (renamed from
`books/Homelab` 2026-10-30; migrated once on the first run). PDFs **mirror the vault
folder tree** (real names, spaces and all). One locally-built image runs **unprivileged
as `1000:1000`** (the vault mirror's owner) as **two services** on **kdk-dkr-01**:

- **Watcher (`obsidian-to-koreader-watcher`, long-running):** polls the request queue
  and delivers a "Send to reMarkable" tap **within seconds**. When the tablet is asleep
  it backs off and retries.
- **Scheduled one-shot (`obsidian-to-koreader`, Komodo procedure every 6 h):** drains
  the queue as a **fallback** (if the watcher is down), then reconciles the whole tree —
  `SYNC_FOLDERS` (default `20.Work`) **note-by-note** (hash-gated, delete-on-disappear
  leaving `.sdr`), and every other top-level section as one **combined** PDF.

The **Vault channel** is request-driven: the
[`obsidian-send-to-remarkable`](../../remarkable/obsidian-send-to-remarkable/) plugin drops
a JSON request into `<vault>/_remarkable/queue/`; the lab mirror gets it through Obsidian
Sync (no network/token), the job renders the chosen **scope** (a note, a folder, or the
whole vault) and pushes it, then writes the result to `_remarkable/done/`. Mermaid blocks
render to SVG at build time via the internal [[kroki]] service.

A `/state` lock keeps the watcher and the one-shot from processing the same request twice.

**Related:** [[kdk-dkr-01]] · [[kroki]] · [[calibre]] · [[obsidian-headless]] · remarkable-ocr · koreader-highlights

## At a glance

| Field | Value |
|---|---|
| Purpose | Vault notes → PDF → KOReader on the tablet (outbound leg of the notes loop) |
| URL | — (no ingress; SSH to the tablet, tailnet-first then LAN) |
| Host | [[kdk-dkr-01]] |
| Stack | `stacks/obsidian-to-koreader` (watcher + one-shot, both `obsidian-to-koreader.py`, `MODE` selects) |
| Image | `kdk/obsidian-to-koreader:latest` (built locally — `auto_pull = false`, `run_build = true`) |
| Renderer | pandoc + **WeasyPrint** (`weasyprint==70.0` + `pydyf==0.12.1`); Mermaid → SVG via [[kroki]]; see the stack README bake-off |
| Dest | `/home/root/books/kodiak-codex/<vault path>` (mirrors the vault tree; renamed from `books/Homelab`) |
| Data | `/opt/kdk-lab/obsidian-to-koreader` (state + build), vault `_remarkable/` queue |
| Deploy | watcher runs continuously; Komodo procedure `obsidian-to-koreader` reconciles `0 48 */6 * * *` (after docs-to-vault :42) |
| Blast radius | tablet misses new note PDFs; existing PDFs + all return paths unaffected |

## Verify

```bash
# the procedure's last run logs (on kdk-dkr-01)
ssh kdkadmin@10.1.20.20 'docker logs obsidian-to-koreader --tail 20'
# the watcher's heartbeat + recent deliveries
ssh kdkadmin@10.1.20.20 'docker logs obsidian-to-koreader-watcher --tail 20'
# tablet has the vault PDFs (when awake); tailnet first, LAN fallback
ssh root@100.103.233.103 'ls -R /home/root/books/kodiak-codex' 2>/dev/null \
  || ssh root@10.1.30.245 'ls -R /home/root/books/kodiak-codex'
```

A plugin "send" is picked up by the **watcher within seconds** (tablet awake). If the
watcher is ever down, the 6-hourly procedure sweeps the queue; run the Komodo
`obsidian-to-koreader` procedure by hand to force a sweep.

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| "tablet asleep; skipping" and request stays in `queue/` | tablet off Wi-Fi/tailnet (expected) | retries next run; `ssh root@10.1.30.245 echo ok` when awake |
| Request in `done/` with `status: failed` | bad scope/path, empty note, or render error | read `done/<id>.json` → `status_detail` |
| Request `status: partial` | some PDFs delivered, others skipped (size guard / render) | `status_detail` lists the failures |
| Image missing in a PDF | embed path unresolved; WeasyPrint warns, does not abort | check the embed exists in the vault; text still renders |
| Dataview block shows a placeholder | Dataview is dynamic, headless-blind (by design) | expected — view the live note in Obsidian |
| Mermaid diagram shows as source text with a note | [[kroki]] unreachable/errored (fail-soft; never fails the note) | `docker ps` kroki; `curl` test in `docs/services/kroki.md` |
| Watcher shows `unhealthy` | `/state/watcher.heartbeat` stale > 2 min (loop stuck/stopped) | `docker logs obsidian-to-koreader-watcher` |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[obsidian-headless]] vault mirror (`/opt/kdk-lab/obsidian/vault`) | nothing to render |
| Needs | the tablet reachable over SSH (tailnet/LAN) + `RM_PW` | no delivery (requests stay queued) |
| Needs | [[kroki]] on the `kroki_default` network | Mermaid diagrams fall back to source text (does not fail the note) |
| Needed by | the owner reading notes on the tablet in KOReader | — |
| Pairs with | `remarkable-ocr`, `koreader-highlights` (the return loop) | annotations would not come back |

## Observability

No dashboard. Logs ship to Loki via the observability agent; failures page
Pushover (priority 1). Per-request outcomes live in `_remarkable/done/`.

## Architecture

One locally-built image (`python:3.12-slim` + pandoc + WeasyPrint + openssh/sshpass),
run as two services selected by `MODE`: a long-running **watcher** (polls the queue,
`restart: unless-stopped`, `/state` heartbeat healthcheck) and the scheduled **one-shot**
(drains the queue as fallback, then reconciles the tree). A `fcntl` lock on
`/state/queue.lock` serialises queue processing so neither processes a request twice;
each render runs in its own `/state/build` temp dir so concurrent renders never collide.
The vault is mounted **read-only** for content; the only writable vault path is
`_remarkable/` (a nested read-write mount) so a processed request can move from `queue/`
to `done/`. The container runs **unprivileged as `1000:1000`** (the vault mirror's owner);
`pre_deploy` (root) pre-creates `_remarkable/{queue,done}` as `1000:1000` and chowns
`/state`. Obsidian syntax is normalised before pandoc (wikilinks → text, callouts →
labelled blockquotes, image embeds → resolved absolute paths, note embeds dropped,
**Mermaid → SVG via [[kroki]]** with a source-text fallback, Dataview → placeholder, `%%`
comments stripped). Delivery is **`cat`-over-SSH** (one shell-quoted remote arg, so
spaces/apostrophes/unicode in paths are correct), tailnet (`100.103.233.103`) first then
LAN (`10.1.30.245`), opportunistic (the tablet sleeps; probe with SSH, not ICMP). PDFs
mirror the vault folder tree under `RM_DEST`.

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
| `stacks/obsidian-to-koreader/compose.yaml` | two services (`user: 1000:1000`, `MODE` watch/oneshot), RO vault + RW `_remarkable/` mounts, external `kroki_default` net, `RM_DEST`/`RM_DEST_OLD`, `SYNC_FOLDERS`, `KROKI_URL`, `MERMAID_FORCE_TD`, `POLL_SECONDS`, `SLEEP_BACKOFF_MAX`, `RM_HOST_*`, `PANDOC_ENGINE`, `MAX_PDF_MB` |
| Komodo `pre_deploy` | root: `install -d -o 1000` the queue dirs + chown `/state`, then `op inject` |
| Deploy order | **[[kroki]] first** (creates the `kroki_default` network), then this stack |
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
*Send whole vault*. The watcher delivers within seconds (tablet awake). Read the PDFs in
KOReader under `books/kodiak-codex`, browsing into the matching vault subfolder (the tree
is mirrored). Annotate freely — handwriting and highlights return to the vault via the
jobs above.
