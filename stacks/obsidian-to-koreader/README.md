# obsidian-to-koreader

Render Obsidian vault notes to PDF and put them on the reMarkable for KOReader —
the "read your notes/docs on the tablet" loop. Two modes in one one-shot run:

## 1. Vault channel (request-driven — the "Send to reMarkable" plugin)

The [`obsidian-send-to-remarkable`](../../remarkable/obsidian-send-to-remarkable/)
Obsidian plugin drops a small JSON **request** into `<vault>/_remarkable/queue/`. Because
the lab mirror of the vault (`/opt/kdk-lab/obsidian/vault`) receives that file through the
existing headless **Obsidian Sync**, the plugin needs **no network access, endpoint or
token** — the vault *is* the transport. Each run:

1. reads every `queue/*.json`, renders the requested **scope** to PDF(s),
2. pushes them to KOReader under `RM_DEST` (`/home/root/books/Homelab`) over SSH, then
3. moves the request to `_remarkable/done/<id>.json` with a `status` line, which the plugin
   reads back (through the same sync) to show the last result.

If the tablet is **asleep**, requests are **left in `queue/`** and retried next run (nothing
is lost). The only vault path this job writes is `_remarkable/` (a nested read-write mount);
all other vault content is mounted **read-only**.

### Request schema (`_remarkable/queue/<id>.json`)

| Field | Values | Meaning |
|---|---|---|
| `scope` | `note` \| `folder` \| `vault` | what to send |
| `path` | vault-relative path | the note (`*.md`) or folder; ignored for `vault` |
| `mode` | `combined` \| `per-note` | one PDF, or one PDF per note/section (folder/vault) |
| `target_subdir` | string (optional) | extra subfolder under `RM_DEST` |
| `id`, `created`, `source` | metadata | `id` defaults to the filename stem |

`path` is resolved inside the vault and anything escaping it is refused. A scope matching no
notes, an oversize PDF (`> MAX_PDF_MB`) or a render error is recorded per request as
`status: failed`/`partial` in `done/` (and paged via Pushover).

## 2. Scheduled whole-section sync (the default — unchanged)

When the queue is **empty**, every run reconciles each top-level vault **section** matching
`SECTION_REGEX` (default numbered domains `00.Inbox`, `10.Homelab`, …) into one PDF per
section under `RM_DEST`, exactly as this stack always has: rebuild changed sections (by
content hash), remove PDFs for deleted sections, skip a section over the size guard. So
**nothing regresses** when the plugin is not in use.

## Transport

SSH/scp to the tablet, **tailnet first** (`RM_HOST_TAILNET`, the kernel-mode Tailscale node
`remarkable-pp` = `100.103.233.103`, reachable off-LAN) then **LAN** (`RM_HOST_LAN`,
`10.1.30.245`). Opportunistic: the tablet sleeps and drops Wi-Fi/tailnet, so delivery only
happens when it is awake; probe is SSH (ICMP is filtered). Read-only on vault content;
Pushover on failure. `RM_PW` is op-injected (`op://kdk-cluster/remarkable-device/password`).

## Renderer: WeasyPrint (Phase 1 decision)

Markdown → PDF is host-side. Obsidian syntax is normalised **before** pandoc, so the feature
handling below is the same whichever engine renders; the engine only changes typography and
image handling. A bake-off (2026-10-30) rendered three real vault notes — a 149-line project
note with 2 SVG embeds + a callout + 13 wikilinks, a reading index with 8 wikilinks + a
Dataview block, and a homelab index with 3 Dataview blocks — with the current **WeasyPrint**
path and with **Typst** (`pandoc/typst:3.5.0.0`, pinned).

**Decision: keep WeasyPrint for Phase 1.** It already renders every vault section in
production (pinned `weasyprint==70.0` + `pydyf==0.12.1`), resolves absolute image paths and
raw HTML/CSS natively, and degrades gracefully (a missing image warns, it does not abort).
Typst rendered cleanly and faster and is a single static binary, but it needs
`--pdf-engine-opt=--root=/` before it will resolve absolute image paths (pandoc's Typst
writer rebases them under the working dir otherwise) and is a new toolchain to maintain for
no feature gain here. Typst is the recommended re-evaluation if render time or the `pydyf`
pin ever become painful (Phase 2).

### Obsidian-feature matrix (both engines, driven by `clean_md`)

| Feature | Handling | Rendered? |
|---|---|---|
| YAML frontmatter | stripped | removed (intentional) |
| `[[note]]` / `[[note\|alias]]` wikilinks | → plain text / alias text | yes, as text (no intra-PDF anchor) |
| `![[image.png/.svg]]` embeds | resolved vault-wide → absolute `![]()` | yes (WeasyPrint); Typst needs `--root=/` |
| `![[other note]]` (note embeds) | dropped | no (by design — avoids recursion/bloat) |
| `> [!type] Title` callouts | → `> **TYPE — Title**` blockquote | yes, as a labelled blockquote (no colour/icon) |
| ` ```dataview ` blocks | → `*[Dataview query — renders only in Obsidian]*` | placeholder only (dynamic; headless-blind) |
| `%% comments %%` | stripped | removed |
| standard Markdown (headings, lists, tables, code, images) | pandoc | yes |

Bake-off page counts (A5): project note 20 pp (2.1 MB, SVGs embedded), reading index 4 pp,
homelab index 1 pp — all well under the 40 MB guard.

> **Not live-validated in the bake-off sandbox:** the WeasyPrint *image-embed* render was not
> re-run here because pandoc segfaults under the dev laptop's Docker Desktop VM (an
> environment artifact — the same image renders every section in production on `kdk-dkr-01`).
> WeasyPrint image fidelity should be eyeballed on the first real deploy run. Typst renders
> (incl. the SVG note) were validated.

## The return loop (handwriting + highlights)

This stack is the *outbound* leg only. Annotations come back through the existing jobs, not a
second return path:

- **Handwriting** on these PDFs (pen on the Paper Pro): `remarkable-ocr` OCRs new notebook
  pages (`qwen2.5vl`) and writes one note per page into the matching vault section.
- **KOReader highlights** on these PDFs: `koreader-highlights` pulls each `.sdr` sidecar and
  writes one note per book into `30.Books/Highlights/`.

Both land in the headless vault and sync to every device; neither overwrites the source note.

**Secret:** `RM_PW` (`op://kdk-cluster/remarkable-device/password`), op-injected into `.env`.
