# obsidian-to-koreader

Render Obsidian vault notes to PDF and put them on the reMarkable for KOReader —
the "read your notes/docs on the tablet" loop. ONE locally-built image runs as **two
compose services** sharing the same code:

- **`obsidian-to-koreader-watcher`** — a long-running watcher (`restart: unless-stopped`)
  that polls the request queue and delivers a "Send to reMarkable" tap **within seconds**.
- **`obsidian-to-koreader`** — the scheduled one-shot (Komodo procedure, every 6 h) that
  drains the queue as a **fallback** (if the watcher is down) and then runs the **whole-tree
  reconcile** (standing per-note sync of `SYNC_FOLDERS` + a combined PDF per other section).

A `/state` lock (`fcntl.flock` on `queue.lock`) keeps the watcher and the one-shot from ever
processing the same request twice. `MODE` (`watch` / `oneshot`) selects the behaviour.

Everything is delivered into a folder tree that **mirrors the vault**: a note at
`1. Projects/foo/Bench Layout.md` lands at
`RM_DEST/1. Projects/foo/Bench Layout.pdf` — the **real name with spaces is kept** (KOReader
shows names as-is). Combined-mode PDFs go **inside the folder they cover**.

## 1. Vault channel (request-driven — the "Send to reMarkable" plugin)

The [`obsidian-send-to-remarkable`](../../remarkable/obsidian-send-to-remarkable/)
Obsidian plugin drops a small JSON **request** into `<vault>/_remarkable/queue/`. Because
the lab mirror of the vault (`/opt/kdk-lab/obsidian/vault`) receives that file through the
existing headless **Obsidian Sync**, the plugin needs **no network access, endpoint or
token** — the vault *is* the transport. Each run:

1. reads every `queue/*.json`, renders the requested **scope** to PDF(s),
2. pushes them to KOReader under `RM_DEST` (`/home/root/books/kodiak-codex`) over SSH, then
3. moves the request to `_remarkable/done/<id>.json` with a `status` line, which the plugin
   reads back (through the same sync) to show the last result.

If the tablet is **asleep**, requests are **left in `queue/`** and retried — the watcher
backs off (probing the asleep tablet at most every `SLEEP_BACKOFF_MAX` seconds, default 5 min,
not every poll) and delivers as soon as the tablet is awake; nothing is lost. The only vault
path this job writes is `_remarkable/` (a nested read-write mount); all other vault content is
mounted **read-only**.

The container runs **unprivileged as `1000:1000`** — the same owner as the vault mirror and
the `obsidian-headless` sync agent — so the request files and the delivered `done/` records
are owned like the rest of the vault and the bidirectional Obsidian Sync can manage them.
`pre_deploy` (root) pre-creates `_remarkable/{queue,done}` as `1000:1000` and chowns `/state`,
so the dirs exist with the right owner before the one-shot runs. Either order works: if the
desktop plugin creates `_remarkable/queue/` first, it arrives through the headless sync as
uid 1000 anyway.

### Request schema (`_remarkable/queue/<id>.json`)

| Field | Values | Meaning |
|---|---|---|
| `scope` | `note` \| `folder` \| `vault` | what to send |
| `path` | vault-relative path | the note (`*.md`) or folder; ignored for `vault` |
| `mode` | `combined` \| `per-note` | one PDF, or one PDF per note/section (folder/vault) |
| `target_subdir` | string (optional) | deliver flat into this subfolder under `RM_DEST` (keeping the real name) **instead of** mirroring the vault tree |
| `id`, `created`, `source` | metadata | `id` defaults to the filename stem |

Delivery mirrors the vault tree by default (`RM_DEST/<vault dir>/<note name>.pdf`); a combined
PDF goes inside the folder it covers (`RM_DEST/<folder>/<folder>.pdf`). `path` is resolved
inside the vault and anything escaping it is refused. A scope matching no notes, an oversize
PDF (`> MAX_PDF_MB`) or a render error is recorded per request as `status: failed`/`partial`
in `done/` (and paged via Pushover). A `done/` record's `delivered` list holds the full tablet
paths, e.g. `/home/root/books/kodiak-codex/1. Projects/garage-gladiator-husky/Bench Layout.pdf`.

## 2. Scheduled whole-tree reconcile (the one-shot default)

The scheduled one-shot reconciles the whole tablet tree every 6 h (and is the fallback that
drains the queue if the watcher is down):

- **`SYNC_FOLDERS` (standing per-note sync, default `20.Work`)** — a comma-separated list of
  vault folders mirrored **note-by-note** into the tree (`RM_DEST/<vault path>/<note>.pdf`).
  Only notes whose content hash changed are re-rendered (a per-note hash is kept in the state
  file); a tablet PDF whose source note disappeared is removed (its `.sdr` highlight sidecar is
  **left in place**).
- **Every other top-level section** matching `SECTION_REGEX` (default numbered domains
  `00.Inbox`, `10.Homelab`, …) → one **combined** PDF at `RM_DEST/<section>/<section>.pdf`
  (rebuilt by content hash, removed when the section is deleted, skipped over the size guard).
  A section that `SYNC_FOLDERS` already mirrors per-note is **not** also dumped combined.

So nothing regresses when the plugin is not in use, and the work section stays mirrored
note-by-note without a tap.

### Migrations (first run, one-time)

On the first reconcile the job performs two idempotent migrations, logged and never
destructive:

1. **Folder rename** — if `RM_DEST_OLD` (`/home/root/books/Homelab`) exists and `RM_DEST`
   (`/home/root/books/kodiak-codex`) does not, it `mv`s the old folder over (keeping any `.sdr`
   sidecars). It never deletes.
2. **Flat → tree** — it removes the old **flat** PDFs sitting directly under `RM_DEST` (the old
   per-section dumps and one-off sends such as `Bench-Layout.pdf`), leaving `.sdr` sidecars and
   subdirectories, then rebuilds everything into the mirrored tree. Tracked by a `layout`
   marker in the state file so it runs once.

## Environment knobs

Set in `compose.yaml` (shared `x-env` anchor); the two services differ only in `MODE`.

| Env | Default | Meaning |
|---|---|---|
| `MODE` | `oneshot` | `watch` = the long-running watcher; `oneshot` = the scheduled fallback + reconcile |
| `RM_DEST` | `/home/root/books/kodiak-codex` | tablet root for the mirrored vault tree |
| `RM_DEST_OLD` | `/home/root/books/Homelab` | one-time rename source (`mv` on first run; never deleted; empty disables) |
| `SYNC_FOLDERS` | `20.Work` | comma-separated vault folders mirrored note-by-note every scheduled run |
| `KROKI_URL` | `http://kroki:8000` | internal Kroki service for Mermaid → SVG |
| `MERMAID_FORCE_TD` | `1` | rewrite wide (`LR`/`RL`) flowcharts to top-down for the narrow page |
| `POLL_SECONDS` | `20` | watcher queue poll interval |
| `SLEEP_BACKOFF_MAX` | `300` | watcher: probe an asleep tablet at most this often (seconds) |
| `PDF_PAGE_SIZE`, `MAX_PDF_MB`, `SECTION_REGEX`, `RM_HOST_*`, `RM_USER` | — | as before (device page size, size guard, which sections, tablet addresses) |

## Transport

SSH to the tablet, **tailnet first** (`RM_HOST_TAILNET`, the kernel-mode Tailscale node
`remarkable-pp` = `100.103.233.103`, reachable off-LAN) then **LAN** (`RM_HOST_LAN`,
`10.1.30.245`). Opportunistic: the tablet sleeps and drops Wi-Fi/tailnet, so delivery only
happens when it is awake; probe is SSH (ICMP is filtered). Read-only on vault content;
Pushover on failure. `RM_PW` is op-injected (`op://kdk-cluster/remarkable-device/password`).

Files are copied with **`cat`-over-SSH** (`ssh … "cat > <shlex-quoted path>"`), not `scp`:
the remote path is a single shell-quoted argument, so names with spaces, apostrophes and
unicode land correctly on the tablet's shell regardless of its scp protocol (modern scp uses
SFTP and would pass the path literally; dropbear's legacy SCP shell-parses it). `mkdir -p` and
`rm -f` are shell-quoted the same way.

## Troubleshooting

- **Every run logs "tablet asleep; skipping" even though the tablet's port 22 is open, and
  `ssh`/`sshpass` inside the container print `No user exists for uid 1000` and return
  immediately.** OpenSSH's client refuses to run for a uid that has no `/etc/passwd` entry,
  so `pick_host()` never gets a login and treats the tablet as asleep. The image bakes a
  matching `app` user (uid/gid 1000) for this reason; if you see this symptom, the image was
  built without that user. Verify with
  `docker run --rm --user 1000:1000 --entrypoint sh kdk/obsidian-to-koreader:latest -c 'id; ssh -o BatchMode=yes -o ConnectTimeout=3 root@127.0.0.1 true'`
  — `id` must resolve to `app` and ssh must give a *connection* error, not "No user exists".
- **A request fails with `pandoc failed: pandoc: .: openTempFile: permission denied`.**
  pandoc writes its temp files in the current working directory; unprivileged (uid 1000) that
  would be the root-owned image WORKDIR. `render_pdf()` runs pandoc with `cwd=/state/build`
  (writable) and `TMPDIR=/tmp`, and the image sets `WORKDIR /tmp`. If you see this, the image
  predates that fix or `/state` is not owned by 1000 (pre_deploy chowns it).
- **A PDF is delivered (the `done/` record is `ok`) but KOReader says it cannot open it / the
  file is missing.** KOReader's file browser was open on the folder before the delivery and is
  showing a cached listing. Leave and re-enter the folder, or restart KOReader (verified by the
  owner 2026-10-30). Same for Readwise deliveries.
- **The vault's folder on the tablet is `books/kodiak-codex` (renamed from `books/Homelab`
  2026-10-30).** The first reconcile `mv`s the old folder over once (keeping `.sdr` sidecars,
  never deleting) and then mirrors the vault tree underneath it, so open KOReader →
  `books/kodiak-codex` and browse into the matching vault subfolder. `books/Readwise` and other
  book libraries are untouched.
- **A page shows as two half-screens / "cut off" in KOReader.** The PDF page is larger than the
  screen (e.g. an older A4 render). New PDFs are sized to the panel (`PDF_PAGE_SIZE`); for an
  existing document use KOReader zoom mode *fit page* (tap top of screen → zoom) and *set as
  default*.

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
| `![[image.png/.svg]]` embeds | resolved vault-wide → **percent-encoded** absolute `![]()` | yes (WeasyPrint; SVG rasterised natively). Paths with spaces/unicode are `%`-encoded — a bare path is truncated at the first space by pandoc's reader, silently dropping the image; Typst needs `--root=/` |
| `![[other note]]` (note embeds) | dropped | no (by design — avoids recursion/bloat) |
| `> [!type] Title` callouts | → `> **TYPE — Title**` blockquote | yes, as a labelled blockquote (no colour/icon) |
| ` ```mermaid ` blocks | rendered to **SVG at build time via Kroki** (`KROKI_URL`, POST to `/mermaid/svg`), embedded like an image; wide `flowchart/graph LR\|RL` → `TD` and `direction LR\|RL` → `TB` for the narrow page (`MERMAID_FORCE_TD=1`) | yes, as an SVG. If Kroki is unreachable/errors, falls back to the diagram **source** (fenced) with a one-line note — never fails the note. Mermaid 11 syntax (quote labels with `@ ( ) { } #`) |
| ` ```dataview ` blocks | → `*[Dataview query — renders only in Obsidian]*` | placeholder only (dynamic; headless-blind) |
| `%% comments %%` | stripped (but **not** inside a Mermaid block — those are parked before stripping, so Mermaid `%%` comments survive) | removed |
| standard Markdown (headings, lists, tables, code, images) | pandoc | yes |

Bake-off page counts: project note 20 pp (2.1 MB, SVGs embedded), reading index 4 pp,
homelab index 1 pp — all well under the 40 MB guard.

### Page sizing for the device

PDFs are rendered **for the Paper Pro screen, not for paper**: an A4/A5 page shows up in
KOReader as two half-screens ("cut off"). `render_pdf()` injects e-ink CSS (via pandoc
`--include-in-header`) with `@page { size: <PDF_PAGE_SIZE>; margin: 9mm 9mm 11mm 9mm }`,
`PDF_PAGE_SIZE` defaulting to **`180mm 240mm`** (the panel is 1620×2160 px at ~229 ppi, 3:4),
base font 12.5 pt / line-height 1.4, `img,svg { max-width:100%; height:auto; page-break-inside:avoid }`,
`table { font-size:0.9em; word-break:break-word }`, headings `page-break-after:avoid`, and no
`@page` headers/footers. Set `PDF_PAGE_SIZE` (e.g. the reMarkable 2 panel, or `A4` for true
paper) in compose to change it. **If a document still shows as halves in KOReader**, use zoom
mode *fit page* (tap the top of the screen → zoom) and *set as default*.

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
