---
title: LazyLibrarian
host: kdk-dkr-01
stack: media
tier: internal
status: live
storage: yes
secrets: [lazylibrarian-api, audiobookshelf-api-root]
updated: 2026-11-08
tags: [homelab/stack, tier/internal, status/live]
---

# LazyLibrarian

The **only** book and **audiobook** manager, behind the DMZ [[seerr|Libreseerr]] request UI. Runs in the [[media]] stack on kdk-dkr-01. It replaced Chaptarr, which was decommissioned 2026-11-03 (the books already on disk under `/mnt/media/books/{ebook,audiobook}` belong to the library and stayed). Every UI is behind Authelia ForwardAuth; the `/api` path is bypassed so the DMZ Libreseerr can use the API with a key.

**Related:** [[kdk-dkr-01]] · [[media]] · [[seerr]] · [[audiobookshelf]] · [[traefik]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Add **one requested book/audiobook at a time** (vs Chaptarr, which it replaced, which followed a whole author's catalogue) |
| Status | 🟢 Live — sole book/audiobook manager (replaced Chaptarr, decommissioned 2026-11-03); owner completes the post-deploy UI setup below |
| Host | [[kdk-dkr-01]] |
| Endpoint | `https://lazylibrarian.kmkdp.com` (LAN/Tailscale only; never on the Cloudflare tunnel) |
| Image | `lscr.io/linuxserver/lazylibrarian:fc698c6c-ls388` (upstream rolling git-hash build, 2026-11-02; actively maintained) |
| Deploy | Komodo stack `media` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/media/lazylibrarian` (config); libraries on `/mnt/media/books/{ebook,audiobook}`, downloads `/mnt/media/staging` |
| Blast radius | Independent of Sonarr/Radarr; no existing library touched |

## Why it exists

Chaptarr (a Readarr fork), which this replaced, followed **whole authors**: adding one monitored the entire catalogue (George R.R. Martin pulled 2,973 of 3,065 works, including translations and box sets) and was slow enough that Libreseerr's 30 s calls timed out. LazyLibrarian adds a single requested title and marks only that title Wanted. Libreseerr already ships a LazyLibrarian backend (`/app/lazylibrarian.py`), so the switch was a config change on the Libreseerr side. Chaptarr was decommissioned 2026-11-03.

## Verify

```bash
# From a LAN/tailnet host:
curl -s -o /dev/null -w '%{http_code}\n' https://lazylibrarian.kmkdp.com/          # 302 -> SSO
# API bypass (needs the API key; see Secret Rotation):
LL_API_KEY=$(op read 'op://kdk-ops/lazylibrarian-api-rw/credential')
curl -s "https://lazylibrarian.kmkdp.com/api?cmd=getVersion&apikey=$LL_API_KEY"     # version string
# On kdk-dkr-01:
ssh kdkadmin@10.1.20.20 "sudo docker ps --filter name=lazylibrarian --format '{{.Status}}'"   # healthy
```

## Post-deploy setup (owner, in the LazyLibrarian web UI)

Do these **in order** after the first deploy. Nothing here is templated from the repo — LazyLibrarian owns its own `config.ini`; these are UI steps. Menu paths are `Config -> <tab>`.

### 0. Host pre-create (hand step, before the first deploy)

```bash
ssh kdkadmin@10.1.20.20
sudo mkdir -p /opt/kdk-lab/media/lazylibrarian
sudo chown -R 1000:1000 /opt/kdk-lab/media/lazylibrarian
```

### 1. Downloader — SABnzbd (`Config -> Downloaders`)

- Enable **Use NZB**, add a **SABnzbd** downloader.
- Host `sabnzbd`, Port `8080` (same Docker network; or `http://sabnzbd:8080`).
- API key: SABnzbd's API key (SABnzbd UI -> Config -> General).
- Category: `lazylibrarian` (the live value, `sab_cat` in `config.ini`). The SABnzbd category of the same name has folder `lazylibrarian`, so SAB completes into `/data/media/staging/lazylibrarian`, which LazyLibrarian reads as its download directory (step 3), and runs the import-trigger script (see Import trigger).

### 2. Indexers (`Config -> Providers`)

Prefer letting **Prowlarr** push indexers. In Prowlarr (`prowlarr.kmkdp.com`) -> Settings -> Apps -> Add -> **LazyLibrarian**:

- LazyLibrarian Server `http://lazylibrarian:5299`
- API Key: LazyLibrarian's key (step 7)
- Sync — Prowlarr adds its Newznab/Torznab indexers to LazyLibrarian's Providers automatically.

If Prowlarr's app sync does not cover LazyLibrarian in your Prowlarr version, add the indexers by hand in `Config -> Providers` as **Newznab** entries (URL + API key per indexer), which Prowlarr can export.

### 3. Downloaded-book directory (`Config -> Processing -> Folders`)

- **Download Directories:** `/data/media/staging/lazylibrarian` (comma-separated list; the live `download_dir`; this is where SAB drops completed books — the single media mount, so imports hardlink).

### 4. Libraries — the ebook/audiobook split (`Config -> Processing -> Folders`)

| Field (UI label) | Value | config.ini key |
|---|---|---|
| **eBook Library Folder** | `/data/media/books/ebook` | `EBOOK_DIR` |
| **AudioBook Library Folder** | `/data/media/books/audiobook` | `AUDIO_DIR` |
| **Alternate Directory** | `/data/media/staging/ebook` (pinned, see [Pinned settings](#pinned-settings); empty = the Import eBooks button does nothing) | `ALTERNATE_DIR` |

LazyLibrarian keeps ebooks and audiobooks in **separate** base folders — verified in source (`configdefs.py`: `EBOOK_DIR` / `AUDIO_DIR`). This matches [[audiobookshelf|ABS]], which reads only `/mnt/media/books/audiobook`.

### 5. Naming — series folders for ABS (`Config -> Processing -> Name Formatting`)

Goal: produce `Author/Series/Title` for series books and `Author/Title` for standalones (no empty/"Unknown" series segment). LazyLibrarian's `{ ... }` curly blocks collapse to an empty string when a variable inside is empty (verified in the docs: `{$SerName (book $SerNum)}` returns empty if either is empty).

| Field (UI label) | Value | config.ini key |
|---|---|---|
| **Series Name Pattern** (`$FmtName`) | `$SerName` | `SERIES_FORMAT` |
| **eBook Foldername Pattern** | `$Author/{$SerName/}$Title` | `EBOOK_DEST_FOLDER` |
| **eBook Filename Pattern** | `$Title{ ($SerName #$SerNum)}` | `EBOOK_DEST_FILE` |
| **AudioBook Foldername Pattern** | `$Author/{$SerName/}$Title` | `AUDIOBOOK_DEST_FOLDER` |
| **AudioBook Filename Pattern** | `$Title - Part $Part` (multi-part) | `AUDIOBOOK_DEST_FILE` |
| **AudioBook Filename Pattern for single file** | `$Title` | `AUDIOBOOK_SINGLE_FILE` |

Pattern check (verified against the variable semantics in `config_processing`):

- **Expeditionary Force #6 "Mutiny"** (series): `SerName=Expeditionary Force`, `Title=Mutiny` -> `Craig Alanson/Expeditionary Force/Mutiny/`.
- **"Radical Candor"** (standalone): `SerName` empty -> the `{$SerName/}` block collapses -> `Kim Scott/Radical Candor/`. No empty or "Unknown" segment.

> LazyLibrarian has **no `$Narrator` variable** for folders, so it cannot reproduce the existing ` - <Narrator>` suffix (e.g. `Aftermath - R.C. Bray`). New LazyLibrarian audiobook folders are `Author/Series/Title`. This is cosmetic for ABS — series grouping comes from the folder path and the OPF (below), not the narrator segment. Narrator is still captured inside the OPF/metadata (`lazylibrarian:narrator`, from Audnexus enrichment).

### 6. Series + OPF, and the no-whole-author rule (`Config -> Processing`)

**Create the ebook OPF** (`-> Calibre` / Name Formatting area): tick **Create opf files for ebooks** (`IMP_EBOOKOPF`, on by default). This writes `calibre:series` + `calibre:series_index` for ebooks. **Audiobooks get their OPF from the hook, not from LazyLibrarian — see [Audiobookshelf series + rescan](#audiobookshelf-series--rescan).**

**Series info** (`-> Processing`): tick **Add Series Info** so LazyLibrarian records the series name/number (needed for the folder pattern and the hook).

**Turn OFF whole-author / all-books monitoring** (`-> Processing -> Status`) — this is the whole point versus Chaptarr:

| Field (UI label) | Set to | config.ini key |
|---|---|---|
| New Book Status | **Skipped** | `NEWBOOK_STATUS` |
| New AudioBook Status | **Skipped** | `NEWAUDIO_STATUS` |
| New Authors eBook Status | **Skipped** | `NEWAUTHOR_STATUS` |
| New Authors AudioBook Status | **Skipped** | `NEWAUTHOR_AUDIO` |
| Include other books by new authors | **unticked** | `NEWAUTHOR_BOOKS` |

With these at Skipped, importing an author (which Libreseerr does implicitly when it adds a book) leaves the back-catalogue Skipped; only the specific title Libreseerr marks Wanted is searched. Optionally tick **Search when added** so a requested book searches immediately.

### 7. API key (`Config -> Interface`)

- Tick **Enable API**, click **Generate API key**, Save.
- Store it in 1Password and wire Libreseerr (see [Secret Rotation](#secret-rotation) and [Libreseerr integration](#libreseerr-integration)).

### 8. Audiobookshelf series + rescan

This is wired by a repo-managed hook — `stacks/media/lazylibrarian/hooks/abs-notify.py`, mounted read-only at `/hooks` in the container.

**Why a hook is needed (verified against source):**

- LazyLibrarian writes an OPF **only for ebooks**. `postprocess.py` returns before `create_opf()` for non-ebooks, and the `writeOPF` API keys on `BookFile` (the ebook file) and bails with *"No bookfile found"* for an audiobook. So an audiobook folder never gets an OPF from LazyLibrarian, and it does **not** write a structured series tag into the audio files (only the folder path and, optionally, the title tag carry the series name). Without help, ABS can learn the series **name** from the folder path but never the **number**.
- ABS has no cross-host inotify (it reads the audiobook NFS read-only from the DMZ), so a new import is invisible until a library scan is requested.

**What the hook does**, for AudioBook imports only (`AuxInfo == AudioBook`):

1. Writes `metadata.opf` into the audiobook's top-level folder (`Author/[Series/]Title`, stepping up out of `CD1/`, `Disc 2/` style subfolders) with `calibre:series` + `calibre:series_index`, parsed from the book's `SeriesDisplay` field (which LazyLibrarian passes to the hook), else from LazyLibrarian's `member` rows. When a book has several series rows the hook prefers the author's own, non-Ignored, numbered, biggest series and drops a series in a different script from the title (Hardcover attaches foreign split-volume editions such as `冰與火之歌 Split-Volume edition`); a book whose only series is foreign gets no OPF and ABS falls back to the folder layout. ABS's `metadataPrecedence` ranks `opfFile` above `folderStructure`, so ABS picks up the series name **and** number.
2. `POST https://audiobookshelf.kmkdp.com/api/libraries/<id>/scan` with the ABS admin token, so the new book appears within a scan cycle.

Standalones (no `SeriesDisplay`) get no OPF and no series folder; the scan still runs.

**Enable it (`Config -> Notifications -> Custom`):**

| Field (UI label) | Value | config.ini key |
|---|---|---|
| Use Custom Notification | **ticked** | `USE_CUSTOM` |
| Run on download | **ticked** | `CUSTOM_NOTIFY_ONDOWNLOAD` |
| Custom script | `/hooks/abs-notify.py` | `CUSTOM_SCRIPT` |

Leave "Run on snatch" unticked. The ABS token and library id are supplied to the container from the host (see [Secret Rotation](#secret-rotation)); `ABS_URL`/`ABS_LIBRARY_ID` are set in the compose. Click **Test** — with no books yet it logs and exits 0.

## Pinned settings

LazyLibrarian owns `/opt/kdk-lab/media/lazylibrarian/config.ini` and rewrites it on every save, and the image reads no environment variable for these keys, so the compose cannot set them. The repo pins them **here**; the live file is checked against this table. Live values matched on 2026-11-08 (read-only `grep`).

| config.ini `[section]` key | Pinned value | Why |
|---|---|---|
| `[GENERAL] alternate_dir` | `/data/media/staging/ebook` | the Manage Books → **Import eBooks** button and `cmd=importAlternate` read it; empty = the button silently does nothing (2026-11-08) |
| `[API] book_api` | `HardCover` | primary metadata source, see [Metadata source](#metadata-source) |
| `[API] hc_api` | `True` | HardCover enabled (needs its token, UI → Config, HardCover API) |
| `[API] ol_api` | `True` | OpenLibrary enabled, so `source=OpenLibrary` and the `book_api` flip work during a HardCover block |
| `[IMPORTER] multi_source` | `True` | author refreshes and library scans also try the other enabled sources |

**Check** (read-only, prints only these keys):

```bash
ssh kdkadmin@10.1.20.20 'sudo grep -n -i -E "^(alternate_dir|book_api|hc_api|ol_api|multi_source) *=" /opt/kdk-lab/media/lazylibrarian/config.ini'
```

**Set a drifted value** (hand step). Preferred: the API, so LazyLibrarian writes its own file and nothing is restarted (laptop; key from 1Password, never printed):

```bash
LL_API_KEY=$(op read 'op://kdk-ops/lazylibrarian-api-rw/credential')
curl -sG https://lazylibrarian.kmkdp.com/api --data-urlencode "apikey=$LL_API_KEY" \
  --data-urlencode cmd=writeCFG --data-urlencode group=General \
  --data-urlencode name=alternate_dir --data-urlencode value=/data/media/staging/ebook
# same shape for book_api (group=API); then re-run the Check above
```

Only if the API is down: stop the container first (LazyLibrarian overwrites a file edited under it), open `/opt/kdk-lab/media/lazylibrarian/config.ini` in VS Code Remote-SSH (`kdkadmin@10.1.20.20`), edit the key, then `sudo chown 1000:1000 /opt/kdk-lab/media/lazylibrarian/config.ini && sudo chmod 644 /opt/kdk-lab/media/lazylibrarian/config.ini` and start it. The file stays owned by uid 1000 (the container's `abc` user must write it), not root 600. It holds the API key, the SABnzbd key and the Prowlarr indexer keys: never `cat` it, grep named keys only.

### Metadata source

**Decision (2026-11-08): keep `book_api = HardCover`, with OpenLibrary as the manual fallback.** LazyLibrarian has **no automatic fallback** for its API search/add calls: `findBook`, `addBook`, `addBookByISBN` and `importBook` of an unknown id use only `book_api` (or an explicit `source=`); `multi_source` covers author refreshes and library scans only (`importer.py`, `librarysync.py` at `fc698c6c`).

| Source | Recent technical books | Editions | Series (used by the ABS hook) | Limits / key |
|---|---|---|---|---|
| **HardCover** (chosen) | found all 10 books added 2026-11-07 (it can pick a foreign edition: the Pragmatic Programmer record came with a Polish ISBN); not re-queried 2026-11-08, it needs the token | each edition its own record, so a 2nd edition can be pinned by ISBN | yes (`SeriesDisplay`, `member` rows) | personal token required (expired once, 2026-11-02); per-token rate limit; **any 400/401/403 blocks it for 1 day**, a 500 for 2 h |
| OpenLibrary (fallback) | gaps: the DDIA 2nd-edition ISBNs (978-1098119065 / -058) were not in it on 2026-11-08 | one *work* for all editions (the 2019 Pragmatic Programmer sits under the 1999 work), so a work-id add carries the old edition's title | weak | no key; LazyLibrarian blocks it only 30 s on an error |
| GoogleBooks (not used) | good ISBN coverage | one volume per edition, many duplicates | no | needs the lab's own API key (`gb_api` is empty) and a daily quota; a quota hit blocks it until midnight PT |

The cost of HardCover is its self-block: a single rejected query stops every search for a day without an error-level log line. That is covered by the Loki alert `LazyLibrarianProviderBlocked` (warning, `stacks/loki/rules/media.yml`) and the fallback steps in the [add-a-book runbook](../runbooks/add-a-book.md#metadata-source-blocked) (`source=OpenLibrary`, the `writeCFG book_api` flip, clearing the block).

## Import trigger (SABnzbd → LazyLibrarian)

LazyLibrarian only schedules its `PostProcessor` job **while something is Snatched**, and it can fail to re-add that job after a snatch. On 2026-11-03 `showJobs` listed no PostProcessor and a completed download (*Mavericks*, category `lazylibrarian`) sat ~25 min in `/data/media/staging/lazylibrarian` until `forceProcess` was called by hand.

The fix is **event-driven**: a SABnzbd post-processing script on the `lazylibrarian` category. When a job in that category finishes successfully, SABnzbd runs the script, which calls LazyLibrarian's API over the shared stack network:

```
GET http://lazylibrarian:5299/api?apikey=<key>&cmd=forceProcess
```

so the completed download is imported immediately — no wait for LazyLibrarian's scheduler. `sabnzbd` and `lazylibrarian` share the media stack `default` network, so the service name `lazylibrarian` resolves (same as Prowlarr pointing at `http://lazylibrarian:5299`).

**Repo-managed** — `stacks/media/sabnzbd/scripts/lazylibrarian-process.py`, mounted read-only at `/scripts` in the `sabnzbd` service. Stdlib only. It reads SABnzbd's post-processing status (`SAB_PP_STATUS`, positional `argv[7]`; `0` = OK) and calls `forceProcess` on success, retrying a transient failure up to 3 times (a 401/403 bad key, or an HTTP 200 reply with `{"Success": false}`, is a failure and is not retried). On a **failed** download (status != 0) it forks a detached child that waits `LL_FAIL_DELAY` seconds (default 90) and then calls `forceProcess`: SABnzbd writes the history row only after the script exits, and LazyLibrarian's post-processor ends with a download-status pass that reads that row, marks the snatch Failed and sets the book back to Wanted, so it is searched again instead of staying Snatched. It **always exits 0** so a trigger error never fails SABnzbd's post-processing, and prints exactly one line (SABnzbd shows a script's last stdout line in its history). The API key is never printed.

The LazyLibrarian base URL (`LL_URL`, non-secret) is set in the compose `environment`; the API key (`LL_API_KEY`, secret) comes from the host `env_file` `/etc/kdk/sab-ll.env` with `required: false`, exactly like the `lazylibrarian` service's `/etc/kdk/abs-notify.env` — so a missing file can never stop the media stack.

### Owner hand steps (in order)

1. **Put the LazyLibrarian API key on the host** (op runs only on the owner's laptop; never commit it):

   ```bash
   op read op://kdk-ops/lazylibrarian-api-rw/credential | \
     ssh kdkadmin@10.1.20.20 'sudo tee /etc/kdk/sab-ll.env >/dev/null <<<"LL_API_KEY=$(cat)" && sudo chown root:root /etc/kdk/sab-ll.env && sudo chmod 600 /etc/kdk/sab-ll.env'
   ```

2. **(Optional) inspect SABnzbd's current config** before the UI step — read-only, never printing the `api_key` lines:

   ```bash
   ssh -o BatchMode=yes kdkadmin@10.1.20.20 \
     'sudo grep -nE "^script_dir|^\[\[lazylibrarian\]\]" -A6 /opt/kdk-lab/media/sabnzbd/sabnzbd.ini'
   ```

3. **Redeploy** the `media` stack in Komodo (picks up the compose mount + env).

4. **Set the Scripts Folder** — SABnzbd → **Config → Folders → Scripts Folder** = `/scripts`, Save. (config.ini: `[misc] script_dir = /scripts`.)

5. **Assign the script to the category** — SABnzbd → **Config → Categories → `lazylibrarian`** → **Script** = `lazylibrarian-process.py`, Save. (config.ini: the `[[lazylibrarian]]` category's `script = lazylibrarian-process.py`.)

   Steps 4–5 can instead be done with the SABnzbd API `mode=set_config` (`[misc] script_dir=/scripts` and the category's `script`).

6. **Verify**: snatch (or re-grab) a book; on completion SABnzbd's **History** shows the job's script line `lazylibrarian-process: forceProcess requested for '…' (HTTP 200)` and LazyLibrarian imports it without the ~25 min wait. `docker logs sabnzbd` carries the same line.

## Libreseerr integration

Libreseerr (`/app/lazylibrarian.py`, container on [[kdk-dkr-dmz-01]]) selects this backend when a server's `server_software` is `lazylibrarian`. Switch both the ebook and audiobook backends in `/mnt/appdata/libreseerr/config.json`:

```jsonc
// /mnt/appdata/libreseerr/config.json  (edit on dmz-01; restart the libreseerr container after)
{
  "ebook":     { "url": "https://lazylibrarian.kmkdp.com", "api_key": "<LL key>", "server_software": "lazylibrarian" },
  "audiobook": { "url": "https://lazylibrarian.kmkdp.com", "api_key": "<LL key>", "server_software": "lazylibrarian" }
}
```

Keys confirmed from the running container: each of `ebook` and `audiobook` has `url`, `api_key`, `server_software`. Before the switch both were `readarr` -> `https://chaptarr.kmkdp.com`; after the switch both are `lazylibrarian` -> `https://lazylibrarian.kmkdp.com`. **No key values were read.** The URL reaches LazyLibrarian via the internal Traefik `/api` bypass; the DMZ needs the `extra_hosts`/firewall path `lazylibrarian.kmkdp.com -> 10.1.20.20` (port 443). The firewall IP rule (`allow_ctr libreseerr 10.1.20.20 tcp 443`) is IP-based and already open; the `extra_hosts` entry is added in a separate PR (`fix/libreseerr-lazylibrarian-host`).

**Contract Libreseerr actually uses** (from `lazylibrarian.py`): `GET {url}/api?apikey=<key>&cmd=<cmd>`, with `getVersion` (test), `findBook&name=` (search), `searchItem&item=<isbn>` (ISBN), `findAuthor&name=`, `addBook&id=<bookid>&wait=1`, `getAllBooks&status=|audiostatus=Open|Have` (skip a book already owned in that format), `queueBook&id=<bookid>&type=eBook|AudioBook`, `searchBook&id=<bookid>&type=` (search now, not at the 6-hourly run), `getWanted`, `getSnatched`, `getBooks`.

> **Known limitation — audiobook requests queue as eBook.** Libreseerr's shipped `add_book()` always calls `queueBook ... type=eBook`, for *both* the ebook and audiobook server types (no `type=AudioBook` path). LazyLibrarian's `queueBook` defaults to eBook, so a request made through Libreseerr's **audiobook** backend marks the **eBook** Wanted, not the audiobook. This is a Libreseerr-side gap, not a deploy bug, and is **out of scope for this PR** (Libreseerr is not in this repo). Options for the owner: (a) request audiobooks from inside LazyLibrarian's own UI (mark AudioBook Wanted); or (b) patch Libreseerr's `lazylibrarian.py` to pass `type=AudioBook` when `server_type == "audiobook"`. Flag to the Libreseerr maintainer.

## End-to-end test (owner)

1. In Libreseerr, request the **audiobook** of *A Game of Thrones*. (If the limitation above bites, instead mark the AudioBook Wanted in LazyLibrarian's UI.)
2. In LazyLibrarian -> **Wanted**: confirm **only** that title is Wanted (no other GRRM titles appeared — proves the Skipped statuses work).
3. SAB downloads it; LazyLibrarian post-processes into `/data/media/books/audiobook/George R.R. Martin/A Song of Ice and Fire/A Game of Thrones/`.
4. Confirm the folder holds `metadata.opf` with `<meta name="calibre:series" content="A Song of Ice and Fire"/>` and `<meta name="calibre:series_index" content="1"/>`.
5. Within a scan cycle, ABS (`audiobookshelf.kmkdp.com`) shows the book under series **A Song of Ice and Fire #1**, no duplicate and no "missing" item.
6. Repeat with **Expeditionary Force #6 "Mutiny"** (expect series #6) and a standalone (**"Radical Candor"** — expect no series, no empty series folder).

If the number is wrong/absent, check `SeriesDisplay` in LazyLibrarian (Add Series Info on, series matched) and the hook log (`docker logs lazylibrarian | grep abs-notify`).

## Troubleshooting

| Problem | Cause | Validate |
|---|---|---|
| UI loops to SSO | normal; API clients must use `/api` | `curl ".../api?cmd=getVersion&apikey=…"` returns a version |
| Libreseerr "connection failed" | DMZ can't resolve/reach internal Traefik | DMZ `extra_hosts` must map `lazylibrarian.kmkdp.com -> 10.1.20.20`, firewall `192.168.191.20 -> 10.1.20.20:443` open (same IP path the DMZ already uses) |
| Whole author got monitored | New*/NewAuthor* statuses not Skipped | `Config -> Processing -> Status` — all four = Skipped |
| ABS shows series name but no number | OPF missing or `SeriesDisplay` empty | check `metadata.opf` in the folder; `docker logs lazylibrarian \| grep abs-notify` |
| ABS never shows the new book | scan not triggered / token wrong | hook log shows `ABS scan requested: HTTP 200`; 401/403 = bad token; DNS/firewall to the DMZ |
| Requested audiobook came as an ebook | Libreseerr `queueBook type=eBook` limitation | request in LazyLibrarian UI, or patch Libreseerr (see limitation above) |
| Import copied instead of hardlinked | download dir and library on different mounts | both must be under the one `/mnt/media:/data/media` mount |
| Every `findBook` / `addBookByISBN` says "No results" | metadata source blocked (HardCover: 1 day after a 400) | alert `LazyLibrarianProviderBlocked`; [runbook](../runbooks/add-a-book.md#metadata-source-blocked) |
| GUI **Import eBooks** does nothing | `alternate_dir` empty | [Pinned settings](#pinned-settings) check |
| `importBook` returns `False` for a staged folder | more than one file of one type in the folder (`multibook`) | one book per staging subfolder ([runbook](../runbooks/add-a-book.md#a-staging-import)) |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[media|SABnzbd]] + [[media|Prowlarr]] | downloads + indexers |
| Needs | `/mnt/media` single mount | hardlink imports |
| Needs | [[traefik]] `/api` bypass | Libreseerr API access |
| Feeds | [[audiobookshelf]] | audiobook library (OPF series + rescan via the hook) |
| Feeds | [[seerr|Libreseerr]] | book/audiobook request fulfilment |

## Observability

No dashboard. `lazylibrarian.kmkdp.com` is probed by [[kdk-mon-01]]; container health via the compose `healthcheck`; the watchdog/Prometheus rules cover restart/OOM. Hook activity is in the container log (`grep abs-notify`). Loki rule `LazyLibrarianProviderBlocked` (`stacks/loki/rules/media.yml`, warning) fires when a metadata source is blocked for hours or days. Adding books by hand: [Add a book](../runbooks/add-a-book.md). The `wanted` rows hold the Prowlarr key in their download URLs: never export them ([runbook](../runbooks/add-a-book.md#the-prowlarr-key-in-download-urls)).

## Architecture

Listens on `5299`, joins `default` + `proxy`. Traefik routes `lazylibrarian.kmkdp.com` with ForwardAuth, plus a priority-100 `lazylibrarian-api` router for `/api` without ForwardAuth (DMZ Libreseerr). Config on local SSD (`/opt/kdk-lab/media/lazylibrarian`); libraries + downloads on the single `/mnt/media` NFS mount so imports hardlink. The post-import hook (`/hooks/abs-notify.py`, read-only) writes the audiobook OPF and triggers the ABS rescan. DNS: `dns-reconcile` derives `lazylibrarian.kmkdp.com -> 10.1.20.20` from the `Host()` label; LAN/Tailscale only, never on the Cloudflare tunnel.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/media/compose.yaml` (`lazylibrarian`) | image, volumes, Traefik + `/api` labels, ABS env, hook mount, hardening |
| `stacks/media/compose.yaml` (`sabnzbd`) | `./sabnzbd/scripts:/scripts:ro` mount, `LL_URL` env, `sab-ll.env` env_file (import trigger) |
| `stacks/media/lazylibrarian/hooks/abs-notify.py` | post-import hook: audiobook OPF (series) + ABS rescan |
| `stacks/media/sabnzbd/scripts/lazylibrarian-process.py` | SABnzbd category script: calls LazyLibrarian `forceProcess` on a completed download |
| `/opt/kdk-lab/media/lazylibrarian/config.ini` | all UI settings (owner-managed; not in the repo) |
| `/opt/kdk-lab/media/sabnzbd/sabnzbd.ini` | SABnzbd config incl. `script_dir` + category `script` (owner-managed UI; not in the repo) |
| `/etc/kdk/abs-notify.env` (host) | `ABS_API_TOKEN=…` (op-read; mounted via `env_file`, never committed) |
| `/etc/kdk/sab-ll.env` (host) | `LL_API_KEY=…` for the import trigger (op-read; `env_file` on `sabnzbd`, never committed) |

## Secret Rotation

| Secret | Use | How |
|---|---|---|
| `kdk-cluster/lazylibrarian-api` | LazyLibrarian API key for Libreseerr + Prowlarr | regenerate in `Config -> Interface`, update `op`, re-inject Libreseerr/Prowlarr |
| `kdk-ops/lazylibrarian-api-rw` | LazyLibrarian API key for the SABnzbd import trigger | `op read op://kdk-ops/lazylibrarian-api-rw/credential` → `/etc/kdk/sab-ll.env` as `LL_API_KEY=…` (root:root 600); see [Import trigger](#import-trigger-sabnzbd--lazylibrarian) |
| `kdk-ops/audiobookshelf-api-root` | ABS admin token for the rescan hook | from the host file below |

On the owner's laptop (op runs only there):

```bash
# 1. Store the LazyLibrarian API key (after Generate API key in the UI):
op item create --vault kdk-cluster --category "API Credential" --title lazylibrarian-api \
  'credential=<the LL API key>' 'url=https://lazylibrarian.kmkdp.com'
# (rotate later with: op item edit lazylibrarian-api --vault kdk-cluster 'credential=<new>')

# 2. Put the ABS token on the host for the hook (never commit it):
op read op://kdk-ops/audiobookshelf-api-root/credential | \
  ssh kdkadmin@10.1.20.20 'sudo tee /etc/kdk/abs-notify.env >/dev/null <<<"ABS_API_TOKEN=$(cat)" && sudo chmod 600 /etc/kdk/abs-notify.env && sudo chown root:root /etc/kdk/abs-notify.env'

# 3. Install the LL key into Libreseerr's config on dmz-01 (see Libreseerr integration),
#    then restart the libreseerr container.
```

## Rollback

1. Reverting to Chaptarr is **no longer a quick config flip** — Chaptarr was decommissioned 2026-11-03. It requires reverting the decommission PR (re-add the `chaptarr` service to `stacks/media/compose.yaml` and its Authelia/DNS/dashboard entries), restoring the archived config `/opt/kdk-lab/media/chaptarr-archive-2026-11-03.tgz`, redeploying `media`, then pointing Libreseerr's `ebook`/`audiobook` `server_software` back to `readarr` / `https://chaptarr.kmkdp.com`. This is only possible before the archive is deleted (~1 week grace, see the decommission PR).
2. If removing LazyLibrarian: delete the `lazylibrarian` service (and the hook additions) from `stacks/media/compose.yaml`, redeploy the `media` stack with `--remove-orphans` (already set), and remove `/opt/kdk-lab/media/lazylibrarian` and `/etc/kdk/abs-notify.env` on the host. The ABS library and existing audiobooks are untouched (the hook only adds `metadata.opf` sidecars, which ABS ignores once removed).
