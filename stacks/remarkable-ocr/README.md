# remarkable-ocr

Turns reMarkable handwriting into Obsidian notes. Pages tagged **`@dia`** become a
brand-styled network topology SVG (via `remarkable/diagram/topology_svg.py`).

## Flow (`pipeline.py run`, hourly Komodo procedure)

1. **enqueue** — *if the tablet is on Wi-Fi* (`root@$RM_HOST` dropbear): list the
   notebooks under the **`Kodiak Notebooks`** folder, hash every page's `.rm`
   (`md5sum`), and for each new/changed page: `scp` the stroke file, `rmc` → SVG,
   auto-pick a background by ink color, rasterise to a 600px PNG, and drop it in
   `pending/` with a sidecar (`notebook → vault section`, page uuid, hash).
   Near-blank pages (`< MIN_STROKES` strokes) are skipped — a vision model
   hallucinates text on an empty image.
2. **drain** — *if Ollama is up* (`$OLLAMA_URL`, `qwen2.5vl:7b`): OCR each queued
   page to Markdown; on `@dia`, extract a topology JSON and render the SVG. Write
   **one note per page** into the matching vault section, embedding the SVG and the
   auto-extracted topology JSON (edit + re-run to correct it — the review gate).
   `pending/` → `done/`; failures retry up to `MAX_RETRY` then land in `failed/`.

Either half no-ops when its dependency is offline, so nothing is lost: the tablet
sleeps (drops Wi-Fi/SSH), and the 4090 OCR workstation is intermittent. The queue
under `/opt/kdk-lab/remarkable-ocr/{pending,done,failed}` + `state.json` is durable.

## Notebook → vault section

`Homelab → 10.Homelab`, `Personal → 20.Personal`, `Books → 30.Books`,
`Development → 40.Development`, `Learning → 50.Learning`, `Work → 60.Work`,
anything else → `00.Inbox/Unsorted`. (Mirrors `90.Meta/Routing.md` in the vault.)

## Config (`.env`, op-injected)

`RM_HOST` (tablet LAN IP — reserve it in DHCP so it doesn't drift), `RM_PW`
(`op://kdk-ops/remarkable-device`), `OLLAMA_URL`, `OCR_MODEL`, `RM_FOLDER`.
Tunables: `MIN_STROKES` (3), `MAX_RETRY` (4).

## Run manually

`komodo` → deploy the `remarkable-ocr` stack (or run the `remarkable-ocr`
procedure). Local dry-run: `python pipeline.py enqueue` / `drain` / `run`.
