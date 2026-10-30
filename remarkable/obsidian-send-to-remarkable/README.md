# Send to reMarkable (Obsidian plugin)

A small Obsidian plugin that sends a note, a folder, or the whole vault to the
**reMarkable Paper Pro**. It owns **no transport and no secrets**: it just drops a
JSON *request* into a vault folder (`_remarkable/queue/` by default). The kdk-lab
[`obsidian-to-koreader`](../../stacks/obsidian-to-koreader/README.md) job sees that
file on the lab mirror of the vault — carried there by the vault's existing
**Obsidian Sync** — renders the requested scope to PDF, pushes it to the tablet over
SSH (tailnet first, LAN fallback), and moves the request to `_remarkable/done/` with a
status line, which the plugin reads back to show the last result.

So nothing on your desktop needs the tablet password, the lab network, or an API
endpoint.

**How long does it take?** A lab-side *watcher* polls the queue continuously, so once the
request has synced to the lab mirror (Obsidian Sync) and the tablet is **awake**, delivery
usually lands **within a few seconds to ~20 s**. If the tablet is **asleep** the request
waits in the queue and the watcher retries (backing off to a probe every few minutes); it is
delivered as soon as the tablet is awake. A scheduled job also sweeps the queue every 6 h as a
fallback if the watcher is ever down — so nothing is ever lost.

## Features

- **Ribbon button** and command **"Send active note to reMarkable"**.
- **Command "Send whole vault to reMarkable"**.
- **Folder right-click → "Send folder to reMarkable"** (and note right-click → "Send note…").
- **Settings:** queue folder, done folder, default mode (combined vs per-note), optional
  target subfolder under the tablet's `books/kodiak-codex`.
- Delivered PDFs **mirror your vault's folder tree** under `books/kodiak-codex` (a note at
  `1. Projects/foo/Bench Layout.md` lands at
  `books/kodiak-codex/1. Projects/foo/Bench Layout.pdf`, real name and all), unless you set a
  target subfolder.
- A **Notice** confirming each queued request.
- **Command "Show last reMarkable result"** — reads the newest file in the done folder.

## What a request looks like

The plugin writes `_remarkable/queue/<timestamp>-<rand>.json`:

```json
{
  "id": "20261030-153000-ab12x",
  "scope": "folder",
  "mode": "combined",
  "path": "10.Homelab/Projects",
  "created": "2026-10-30T15:30:00.000Z",
  "source": "obsidian-send-to-remarkable"
}
```

`scope` is `note` (needs `path` to a `.md` file), `folder` (needs `path` to a folder), or
`vault` (no path). `mode` only matters for folder/vault. The lab job validates the path
stays inside the vault and records the outcome in `_remarkable/done/<id>.json`.

## Install (owner's desktop vault)

This plugin installs by **copying the folder** — no release zip, no npm needed, because
`main.js` is committed next to `manifest.json`.

1. Copy this folder into your vault's plugins directory:
   ```bash
   cp -r remarkable/obsidian-send-to-remarkable \
     "~/Documents/kodiak-codex/.obsidian/plugins/obsidian-send-to-remarkable"
   ```
   (Only `manifest.json`, `main.js` and `versions.json` are required at runtime; copying
   the whole folder is fine.)
2. In Obsidian: **Settings → Community plugins → Reload plugins**, then enable
   **Send to reMarkable**. (Community plugins must be turned on; this plugin is not in the
   store, so it loads as an unlisted local plugin.)
3. Open the plugin's settings and confirm the **queue folder** matches the lab job's
   `QUEUE_SUBDIR` (default `_remarkable/queue`).

### First request

1. Open a note and click the tablet ribbon icon (or run **Send active note to reMarkable**).
   You should see a "Queued …" notice and a new file under `_remarkable/queue/`.
2. Wait for the vault to sync; the lab-side watcher then delivers within seconds (if the
   tablet is awake). The request moves to `_remarkable/done/`. (If the watcher is ever down,
   the 6-hourly job sweeps the queue; an admin can also run the Komodo `obsidian-to-koreader`
   procedure by hand to force a sweep.)
3. Run **Show last reMarkable result** to see `ok` and the delivered PDF path, or the error.
4. On the tablet, open KOReader → `books/kodiak-codex` (and into the matching vault
   subfolder) to read it.

## Build from source (optional)

Dependencies are pinned in `package.json`.

```bash
cd remarkable/obsidian-send-to-remarkable
npm ci          # or: npm install
npm run build   # type-checks (tsc) then bundles main.ts -> main.js with esbuild
```

`npm run dev` watches and rebuilds. `node_modules/` is git-ignored; `main.js` is committed.

## Notes

- **Desktop only** (`isDesktopOnly: true`): it writes request files through the vault
  adapter and relies on the lab-side job for delivery.
- It never stores or transmits credentials and makes no network calls of its own.
- The return path (handwriting via `remarkable-ocr`, highlights via `koreader-highlights`)
  is unchanged and documented in `docs/services/obsidian-to-koreader.md`.
