---
title: Calibre
host: kdk-dkr-01
stack: calibre
tier: internal
status: live
storage: yes
secrets: []
updated: 2026-11-09
tags: [homelab/service, tier/internal, status/live, books]
---

# Calibre (calibre.kmkdp.com)

Internal Calibre Content Server on **kdk-dkr-01**, for the **reMarkable only**
(phones and browsers read from the [Audiobookshelf](audiobookshelf.md) `ebooks`
library): serves an ebook library over
**OPDS** so the reMarkable's KOReader pulls books when it comes online (pull
model, replacing the old push-only `book-sync` path). Deliberately **no
Authelia** — OPDS clients can't do SSO, so the LAN/Tailscale is the gate (same
exposure as the legacy raw `:8081`).

**Related:** [[kdk-dkr-01]] · [[traefik]] · [[media]]

## At a glance

| Field | Value |
|---|---|
| Purpose | OPDS ebook library for KOReader / reMarkable (pull model) |
| URL | https://calibre.kmkdp.com (Traefik, **no** Authelia); OPDS catalog `https://calibre.kmkdp.com/opds`; legacy tailnet `http://10.1.20.20:8081/opds` |
| Host | [[kdk-dkr-01]] |
| Stack | `stacks/calibre` (`calibre-server` + `calibre-import`) |
| Image | `kdk/calibre:latest` (built locally — `auto_pull = false`, `run_build = true`) |
| Data | `/opt/kdk-lab/calibre/library` (Calibre library) + `/opt/kdk-lab/calibre/import-state/manifest.json` (importer manifest), both swept by the `/opt/kdk-lab` rsync |
| Deploy | Komodo stack `calibre` (`deploy = true`), webhook + 15-min poll |
| Blast radius | reMarkable can't pull new books; existing downloads unaffected |

## Verify

```bash
ssh kdkadmin@10.1.20.20 'sudo docker ps --filter name=calibre --format "{{.Names}} {{.Status}}"'   # both Up (healthy)
curl -s -o /dev/null -w '%{http_code}\n' https://calibre.kmkdp.com/opds                        # 200 (no SSO redirect)
# books served (through the server) == books in the library database; they must match:
ssh kdkadmin@10.1.20.20 'sudo docker exec calibre-server calibredb --with-library http://localhost:8081/ list -f id | tail -n +2 | grep -c "[0-9]"'
ssh kdkadmin@10.1.20.20 'sudo python3 -c "import sqlite3;print(sqlite3.connect(\"file:/opt/kdk-lab/calibre/library/metadata.db?mode=ro\",uri=True).execute(\"select count(*) from books\").fetchone()[0])"'
#   both 34 on 2026-11-04; 20 after the 2026-11-08 dedupe cleanup (plain `list | wc -l` over-counts: long titles wrap onto extra lines)
```

## Troubleshooting

| Problem | Cause | Command to validate |
|---|---|---|
| `calibre-import exited 137` on every deploy | two services building the same tag, or missing `init` on the loop | handled in compose (`provenance: false`, `init: true`); `docker logs calibre-import` |
| OPDS empty | no books imported yet / wrong source | `docker logs calibre-import` — it scans `/mnt/media/books/ebook` every 900 s; each pass ends `pass done: added=.. failed=..` |
| same book appears again every 15 min, or a book with **no formats** | the pre-2026-11-08 `calibredb add -r` loop (title/author dedupe, lone `.opf` sidecars became books) | fixed by `importer.py` ([Import and dedupe](#import-and-dedupe)); `docker logs calibre-import \| grep -E "^.{0,40}(added\|FAILED)"` should show nothing for files already imported |
| a LazyLibrarian book never appears | importer logged `FAILED <book>` (retried every pass) or recorded it as `duplicate` / `in-library` | `docker logs calibre-import \| grep "<title words>"`; look up its entry in `manifest.json`; force a retry with `--forget` ([below](#force-a-re-import-of-one-file)) |
| 502 at the URL | `calibre-server` not up | `docker logs calibre-server`; server port is 8081 |
| book is in `/mnt/media/books/ebook` but never shows in OPDS | the long-running `calibre-server` serves a cached view; a book that `calibre-import` added **directly to the library on disk** is invisible until the server restarts (and two processes writing the same library can corrupt `metadata.db`) | **fixed**: `calibre-import` now adds *through* the running server from localhost (shares its netns; `--with-library http://localhost:8081/`, server `--enable-local-write`), so OPDS reflects new books immediately. If a stale book predates this change, restart the server once: `docker restart calibre-server`. Confirm on disk vs served: `calibredb --with-library http://localhost:8081/ list \| wc -l` |

## Import and dedupe

`calibre-import` runs `stacks/calibre/importer.py`. Until 2026-11-08 it was
`calibredb add -r --automerge ignore` over the whole tree, which dedupes on
title + authors only: the Pugh PDF (embedded title `0321719492.pdf`) and the SRE
epub (sidecar author `Beyer et al`) looked new on every pass, and LazyLibrarian
sidecars whose basename differs from the ebook's (`OReilly` vs `O'Reilly`, a
dropped comma) were added as books with **no formats**. Every 15 minutes.

Now each pass:

1. **Groups** the ebook files of a directory by basename (calibre's own grouping:
   `name.epub` + `name.mobi` = one book). `.opf` files are never books. A
   directory's sidecar (same basename, or the only `.opf` in a directory with a
   single book) is staged in `/tmp` next to the ebook under the ebook's basename,
   with the directory's images, so calibre uses LazyLibrarian's metadata and cover.
   An ambiguous sidecar is ignored and the embedded metadata wins.
2. **Path dedupe:** skips every file in the manifest whose size + mtime are
   unchanged (not even read). A touched file with the same sha256 is re-stamped,
   not re-added; a file with **new content** is added again.
3. **Content dedupe:** a file whose sha256 equals a format file already in the
   library (read-only mount) is recorded as `in-library` and never sent to
   calibredb. Calibre stores added files byte for byte, so this seeds the
   manifest on the first run and makes a lost manifest harmless.
4. Adds what is left with `calibredb add --automerge ignore -1` (one book per
   call; calibre's title/author check stays as the last guard) and records each
   file with its outcome: `added`, `merged` (new format on an existing book) or
   `duplicate`. A `FAILED` add is not recorded and is retried next pass.

Logs: one line per book (`added <author>/<dir>/<basename> [EPUB] ids=47 opf=…`),
`in library already, recorded: …` for content matches, and
`pass done: added=0 merged=0 duplicate=0 in_library=0 skipped=41 failed=0`.
Manifest entries for files that left the source are dropped (never on an empty
scan, so an unmounted source cannot wipe it). A changed file with the same
title/author as an existing record is passed to calibre again, but
`--automerge ignore` discards a format the record already has; replace a format
by hand with `calibredb add_format`.

### Force a re-import of one file

```bash
# path relative to /mnt/media/books/ebook (the /incoming/ prefix is accepted too)
ssh kdkadmin@10.1.20.20 'sudo docker exec calibre-import python3 /app/importer.py --forget "Ken Pugh/Lean-Agile Acceptance Test-Driven-Development/Lean-Agile Acceptance Test-Driven Development - K. Pugh (Pearson, 2011) WW.pdf"'
```

The next pass picks it up. If Calibre still holds those exact bytes the content
index records it as `in-library` again; delete the Calibre record first
(`calibredb remove <id>` through the server) to really re-add it. To rebuild the
whole manifest, stop `calibre-import`, move `manifest.json` aside and start it:
the content index re-seeds it without adding anything Calibre already has.

### After the 2026-11-08 change (one-off, main session)

1. Merge, let Komodo deploy `calibre` (both containers recreate: the image changed).
   `docker logs calibre-import` shows the first pass: `in library already,
   recorded: …` for each file Calibre holds, `pass done: added=0 … failed=0`
   (or `added=1` per genuinely new LazyLibrarian download).
2. Remove the four re-added duplicates once:
   `docker exec calibre-server calibredb --with-library http://localhost:8081/ remove 43,44,45,46`
   (check the ids first with `list -f id,title,formats`: 43 `0321719492.pdf`, 44 SLO with
   no formats, 45 Terraform with no formats, 46 SRE).
3. Watch the next pass (≤ 15 min): `added=0 … failed=0`, no new ids, and the book
   count from [Verify](#verify) stays at 20 (plus any new download).

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[traefik]] (`proxy` network) | no `calibre.kmkdp.com` ingress / TLS |
| Needs | `/mnt/media/books/ebook` (LazyLibrarian output, `:ro`) | nothing to import |
| Needed by | reMarkable / KOReader | no OPDS pull source |

## Observability

No dedicated dashboard. Container health via Komodo / `docker ps`; logs ship to
Loki via the observability agent.

## Architecture

Two services from one locally-built image (`kdk/calibre`): `calibre-server` runs
`calibre-server --port 8081` (web reader + OPDS at `/opds`) on both `default` and
the external `proxy` network, published on `10.1.20.20:8081`; `calibre-import`
runs `importer.py` (baked into the image), which every `INTERVAL` (900 s) adds each
new ebook from `/mnt/media/books/ebook` (mounted `:ro`) **once** — see
[Import and dedupe](#import-and-dedupe). The import
adds **through the running server**, not the library path on disk, so only one
process writes `metadata.db` and OPDS reflects new books immediately. Calibre only
accepts unauthenticated writes from localhost, so the import **shares the server's
network namespace** (`network_mode: service:server`) and adds via
`http://localhost:8081/`, with the server started `--enable-local-write`; a
cross-container add returns `Forbidden`. The import mounts the library **read-only**
(only to hash format files for the content dedupe; it never opens `metadata.db`)
and `depends_on: server` being healthy. Only `server` builds the image (`import`
reuses the tag) to avoid two image IDs recreating each other on every deploy.
Both services meet the [compose baseline](../reference/compose-baseline.md)
(`no-new-privileges`, `cap_drop: [ALL]`, `read_only` + `tmpfs`, `pids_limit`,
`mem_limit`, healthchecks). Default network pinned to `10.208.22.0/24`
(see [docker-ipam](../reference/docker-ipam.md)).

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/calibre/compose.yaml` | the two services + Traefik labels (no `forwardauth`) |
| `/opt/kdk-lab/calibre/library` | the Calibre library (`metadata.db` + books) |
| `/mnt/media/books/ebook` | LazyLibrarian's ebook output, the import source (`:ro`) |
| `stacks/calibre/importer.py` | the import loop (tests: `stacks/calibre/tests`, CI `calibre-import`) |
| `/opt/kdk-lab/calibre/import-state/manifest.json` | importer manifest (`/state` in the container): one entry per imported source file |
| `INTERVAL`, `STATE_DIR`, `LIBRARY` (import env) | pass interval (900 s), manifest dir (`/state`), read-only library for the content index (`/library`) |
| Komodo stack `calibre` | server kdk-dkr-01, `run_build = true`, webhook on |

## Secret Rotation

No secrets — the server has no auth (LAN/Tailscale is the gate) and the image is
built locally.

## User Guide

Books that LazyLibrarian downloads into `/mnt/media/books/ebook` are imported
automatically within 15 minutes and then show up in the OPDS catalog.

**Add the catalog in KOReader (reMarkable) once:** top menu → the search/globe
icon → **OPDS catalog** → **＋** (add) → set **URL** to `http://10.1.20.20:8081/opds`
and a name like `Calibre`, leave username/password blank, save. Then open it and
**long-press a book → Download** — files land under `books/` on the tablet.

Use the LAN/tailnet address `http://10.1.20.20:8081/opds` (what the tablet's DNS
resolves and what the vault channel uses). `https://calibre.kmkdp.com/opds` also
works (valid `*.kmkdp.com` cert, no SSO), but plain HTTP over LAN/Tailscale avoids
any client-side TLS-validation quirks.
