---
title: LazyLibrarian
host: kdk-dkr-01
stack: media
tier: internal
status: deploying
storage: yes
secrets: [lazylibrarian-api, audiobookshelf-api-root]
updated: 2026-11-02
tags: [homelab/stack, tier/internal, status/deploying]
---

# LazyLibrarian

Book and **audiobook** manager behind the DMZ [[seerr|Libreseerr]] request UI. Runs in the [[media]] stack on kdk-dkr-01, beside [[media|Chaptarr]] (which stays as it is). Every UI is behind Authelia ForwardAuth; the `/api` path is bypassed so the DMZ Libreseerr can use the API with a key.

**Related:** [[kdk-dkr-01]] · [[media]] · [[seerr]] · [[audiobookshelf]] · [[traefik]]

## At a glance

| Field | Value |
|---|---|
| Purpose | Add **one requested book/audiobook at a time** (vs Chaptarr, which follows a whole author's catalogue) |
| Status | 🟡 Deploying — compose + hook merged; owner does the post-deploy UI setup below |
| Host | [[kdk-dkr-01]] |
| Endpoint | `https://lazylibrarian.kmkdp.com` (LAN/Tailscale only; never on the Cloudflare tunnel) |
| Image | `lscr.io/linuxserver/lazylibrarian:fc698c6c-ls388` (upstream rolling git-hash build, 2026-11-02; actively maintained) |
| Deploy | Komodo stack `media` (git-linked, `op inject` pre_deploy) |
| Storage | `/opt/kdk-lab/media/lazylibrarian` (config); libraries on `/mnt/media/books/{ebook,audiobook}`, downloads `/mnt/media/staging` |
| Blast radius | Independent of Chaptarr/Sonarr/Radarr; no existing library touched |

## Why it exists

Chaptarr (a Readarr fork) follows **whole authors**: adding one monitors the entire catalogue (George R.R. Martin pulled 2,973 of 3,065 works, including translations and box sets) and is slow enough that Libreseerr's 30 s calls time out. LazyLibrarian adds a single requested title and marks only that title Wanted. Libreseerr already ships a LazyLibrarian backend (`/app/lazylibrarian.py`), so the switch is a config change on the Libreseerr side.

## Verify

```bash
# From a LAN/tailnet host:
curl -s -o /dev/null -w '%{http_code}\n' https://lazylibrarian.kmkdp.com/          # 302 -> SSO
# API bypass (needs the API key; see Secret Rotation):
curl -s "https://lazylibrarian.kmkdp.com/api?cmd=getVersion&apikey=$LL_API_KEY"     # version string
# On the host:
docker ps --filter name=lazylibrarian --format '{{.Status}}'                        # healthy
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
- Category: `books` (or leave blank). SAB completes into `/data/media/staging`, which LazyLibrarian reads as a download directory (step 4).

### 2. Indexers (`Config -> Providers`)

Prefer letting **Prowlarr** push indexers. In Prowlarr (`prowlarr.kmkdp.com`) -> Settings -> Apps -> Add -> **LazyLibrarian**:

- LazyLibrarian Server `http://lazylibrarian:5299`
- API Key: LazyLibrarian's key (step 7)
- Sync — Prowlarr adds its Newznab/Torznab indexers to LazyLibrarian's Providers automatically.

If Prowlarr's app sync does not cover LazyLibrarian in your Prowlarr version, add the indexers by hand in `Config -> Providers` as **Newznab** entries (URL + API key per indexer), which Prowlarr can export.

### 3. Downloaded-book directory (`Config -> Processing -> Folders`)

- **Download Directories:** `/data/media/staging` (comma-separated list; this is where SAB drops completed books — the single media mount, so imports hardlink).

### 4. Libraries — the ebook/audiobook split (`Config -> Processing -> Folders`)

| Field (UI label) | Value | config.ini key |
|---|---|---|
| **eBook Library Folder** | `/data/media/books/ebook` | `EBOOK_DIR` |
| **AudioBook Library Folder** | `/data/media/books/audiobook` | `AUDIO_DIR` |
| **Alternate Directory** | leave blank (or a scratch import dir) | `ALTERNATE_DIR` |

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

1. Writes `metadata.opf` into the audiobook folder with `calibre:series` + `calibre:series_index`, parsed from the book's `SeriesDisplay` field (which LazyLibrarian passes to the hook). ABS's `metadataPrecedence` ranks `opfFile` above `folderStructure`, so ABS picks up the series name **and** number.
2. `POST https://audiobookshelf.kmkdp.com/api/libraries/<id>/scan` with the ABS admin token, so the new book appears within a scan cycle.

Standalones (no `SeriesDisplay`) get no OPF and no series folder; the scan still runs.

**Enable it (`Config -> Notifications -> Custom`):**

| Field (UI label) | Value | config.ini key |
|---|---|---|
| Use Custom Notification | **ticked** | `USE_CUSTOM` |
| Run on download | **ticked** | `CUSTOM_NOTIFY_ONDOWNLOAD` |
| Custom script | `/hooks/abs-notify.py` | `CUSTOM_SCRIPT` |

Leave "Run on snatch" unticked. The ABS token and library id are supplied to the container from the host (see [Secret Rotation](#secret-rotation)); `ABS_URL`/`ABS_LIBRARY_ID` are set in the compose. Click **Test** — with no books yet it logs and exits 0.

## Libreseerr integration

Libreseerr (`/app/lazylibrarian.py`, container on [[kdk-dkr-dmz-01]]) selects this backend when a server's `server_software` is `lazylibrarian`. Switch both the ebook and audiobook backends in `/mnt/appdata/libreseerr/config.json`:

```jsonc
// /mnt/appdata/libreseerr/config.json  (edit on dmz-01; restart the libreseerr container after)
{
  "ebook":     { "url": "https://lazylibrarian.kmkdp.com", "api_key": "<LL key>", "server_software": "lazylibrarian" },
  "audiobook": { "url": "https://lazylibrarian.kmkdp.com", "api_key": "<LL key>", "server_software": "lazylibrarian" }
}
```

Keys confirmed from the running container: each of `ebook` and `audiobook` has `url`, `api_key`, `server_software` (today both are a 7-char value — `readarr` — pointing at Chaptarr; `url` is 26 chars = `https://chaptarr.kmkdp.com`). **No key values were read; only lengths.** The URL reaches LazyLibrarian via the internal Traefik `/api` bypass; the DMZ needs the same `extra_hosts`/firewall path it already uses for Chaptarr (`lazylibrarian.kmkdp.com -> 10.1.20.20`, port 443).

**Contract Libreseerr actually uses** (from `lazylibrarian.py`): `GET {url}/api?apikey=<key>&cmd=<cmd>`, with `getVersion` (test), `findBook&name=` (search), `searchItem&item=<isbn>` (ISBN), `findAuthor&name=`, `addBook&id=<bookid>`, `queueBook&id=<bookid>&type=eBook`, `getWanted`, `getSnatched`, `getBooks`.

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
| Libreseerr "connection failed" | DMZ can't resolve/reach internal Traefik | DMZ `extra_hosts` must map `lazylibrarian.kmkdp.com -> 10.1.20.20`, firewall `192.168.191.20 -> 10.1.20.20:443` open (same as Chaptarr) |
| Whole author got monitored | New*/NewAuthor* statuses not Skipped | `Config -> Processing -> Status` — all four = Skipped |
| ABS shows series name but no number | OPF missing or `SeriesDisplay` empty | check `metadata.opf` in the folder; `docker logs lazylibrarian \| grep abs-notify` |
| ABS never shows the new book | scan not triggered / token wrong | hook log shows `ABS scan requested: HTTP 200`; 401/403 = bad token; DNS/firewall to the DMZ |
| Requested audiobook came as an ebook | Libreseerr `queueBook type=eBook` limitation | request in LazyLibrarian UI, or patch Libreseerr (see limitation above) |
| Import copied instead of hardlinked | download dir and library on different mounts | both must be under the one `/mnt/media:/data/media` mount |

## Dependencies

| Direction | Thing | What breaks without it |
|---|---|---|
| Needs | [[media|SABnzbd]] + [[media|Prowlarr]] | downloads + indexers |
| Needs | `/mnt/media` single mount | hardlink imports |
| Needs | [[traefik]] `/api` bypass | Libreseerr API access |
| Feeds | [[audiobookshelf]] | audiobook library (OPF series + rescan via the hook) |
| Feeds | [[seerr|Libreseerr]] | book/audiobook request fulfilment |

## Observability

No dashboard. `lazylibrarian.kmkdp.com` is probed by [[kdk-mon-01]]; container health via the compose `healthcheck`; the watchdog/Prometheus rules cover restart/OOM. Hook activity is in the container log (`grep abs-notify`).

## Architecture

Listens on `5299`, joins `default` + `proxy`. Traefik routes `lazylibrarian.kmkdp.com` with ForwardAuth, plus a priority-100 `lazylibrarian-api` router for `/api` without ForwardAuth (DMZ Libreseerr). Config on local SSD (`/opt/kdk-lab/media/lazylibrarian`); libraries + downloads on the single `/mnt/media` NFS mount so imports hardlink. The post-import hook (`/hooks/abs-notify.py`, read-only) writes the audiobook OPF and triggers the ABS rescan. DNS: `dns-reconcile` derives `lazylibrarian.kmkdp.com -> 10.1.20.20` from the `Host()` label; LAN/Tailscale only, never on the Cloudflare tunnel.

## Configuration

| Path / setting | What it is |
|---|---|
| `stacks/media/compose.yaml` (`lazylibrarian`) | image, volumes, Traefik + `/api` labels, ABS env, hook mount, hardening |
| `stacks/media/lazylibrarian/hooks/abs-notify.py` | post-import hook: audiobook OPF (series) + ABS rescan |
| `/opt/kdk-lab/media/lazylibrarian/config.ini` | all UI settings (owner-managed; not in the repo) |
| `/etc/kdk/abs-notify.env` (host) | `ABS_API_TOKEN=…` (op-read; mounted via `env_file`, never committed) |

## Secret Rotation

| Secret | Use | How |
|---|---|---|
| `kdk-cluster/lazylibrarian-api` | LazyLibrarian API key for Libreseerr + Prowlarr | regenerate in `Config -> Interface`, update `op`, re-inject Libreseerr/Prowlarr |
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

1. In Libreseerr `config.json`, set `ebook`/`audiobook` `server_software` back to `readarr` and `url` back to `https://chaptarr.kmkdp.com`; restart libreseerr. Chaptarr resumes serving requests immediately.
2. If removing LazyLibrarian: delete the `lazylibrarian` service (and the `x-hardened`/hook additions) from `stacks/media/compose.yaml`, redeploy the `media` stack with `--remove-orphans` (already set), and remove `/opt/kdk-lab/media/lazylibrarian` and `/etc/kdk/abs-notify.env` on the host. The ABS library and existing audiobooks are untouched (the hook only adds `metadata.opf` sidecars, which ABS ignores once removed).
