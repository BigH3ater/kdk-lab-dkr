# readwise-koreader

Mirror Readwise Reader articles onto the reMarkable **for KOReader** (plain EPUB files),
so highlights you make land in KOReader `.sdr` sidecars and flow to Obsidian via the
`koreader-highlights` job. **Revived 2026-10-30** (Phase 1 of the `remarkable-readwise-app`
design); originally built 2026-09-18 and retired 2026-10-29 for never having been
live-validated or adopted.

- **Source:** Readwise Reader API v3 `/list/`, locations `LOCATIONS` (`new,later,shortlist`),
  keeping only docs carrying `TAG` (default `remarkable`). Selection happens **in Reader**:
  tag a document and it appears on the tablet; untag or archive it and it is removed on the
  next reconcile. The Reader LIST limit is 20 req/min, so the job rate-limits every request
  (`THROTTLE_SECONDS`, default 3.5s) and honours `Retry-After` on a 429.
- **Render:** each article's `html_content` -> EPUB via Calibre `ebook-convert` (images
  inlined). Docs with no HTML body are skipped.
- **Deliver:** copy the EPUB into `RM_BOOKS_DIR` (`/home/root/books/Readwise`) over dropbear
  SSH -- KOReader reads plain files, not xochitl's store. Reconciles: delivers newly-tagged
  docs, removes ones no longer tagged (only files it created, tracked in
  `/opt/kdk-lab/readwise-koreader/readwise-koreader.json`). Leaves `.sdr` sidecars on removal
  so un-ingested highlights survive.
- **Transport:** the tablet is on the tailnet as node `remarkable-pp`. The job SSH-probes the
  tailnet address (`RM_HOST`, `100.103.233.103`) first and falls back to the LAN address
  (`RM_HOST_FALLBACK`, `10.1.30.245`); it uses whichever answers. ICMP is filtered on the
  device, so liveness is an SSH `echo ok`, never `ping`. The tablet sleeps, so delivery is
  opportunistic -- a skipped run reconciles on the next pass.
- **Observability:** it logs to stdout and, because a one-shot exits before the host Alloy can
  scrape it, pushes **one summary line** to Loki directly (`LOKI_URL`, default
  `http://10.1.20.21:3100`), labelled `{job="readwise-koreader", container="readwise-koreader",
  host=…}`. A push failure is logged and never fails the run. Pushover fires on a convert/deliver
  failure, same as the other one-shots (`/etc/kdk/pushover.env`).
- **Why files, not cloud sync:** the native reader can't feed KOReader, and rmfakecloud (the old
  native-reader path) was retired 2026-10-31 along with `readwise-to-remarkable`.

The return path needs no new code: `koreader-highlights` already pulls every `.sdr` under
`/home/root/books` (which includes `Readwise/`) into the vault at `30.Books/Highlights`.
Phase 2 (pushing those highlights back into Readwise) is **not** in this stack yet -- see the
design note for the single-vault-sink decision it depends on.

**Schedule:** every 3h at `:10` (`America/Chicago`), run by the `readwise-koreader` Komodo
procedure (`deploy = false`, `destroy_before_deploy`). Offset from `koreader-highlights` (:05)
and `obsidian-to-koreader` (:48).

**Secrets:** `READWISE_TOKEN` (`op://kdk-cluster/readwise-token-ro/credential`, serves Reader v3
and Highlights v2), `RM_PW` (`op://kdk-cluster/remarkable-device/password`) -- op-injected into
`.env` by the pre_deploy step. Neither value is ever printed.

## Run / validate by hand

From a laptop with `op` (reads the live API, prints counts + titles only, never content):

```bash
READWISE_TOKEN="$(op read op://kdk-cluster/readwise-token-ro/credential)" \
  TAG=remarkable LOCATIONS=new,later,shortlist \
  python3 - <<'PY'
import os,json,urllib.request,urllib.parse
tok=os.environ["READWISE_TOKEN"]
for loc in os.environ["LOCATIONS"].split(","):
    q=urllib.parse.urlencode([("location",loc),("tag",os.environ["TAG"])])
    r=urllib.request.Request("https://readwise.io/api/v3/list/?"+q,
                             headers={"Authorization":f"Token {tok}"})
    d=json.load(urllib.request.urlopen(r))
    print(loc, len(d.get("results",[])), [x.get("title") for x in d.get("results",[])])
PY
```

On the host, one real run (delivers to the tablet if it is awake):

```bash
cd /opt/kdk-lab/stacks/readwise-koreader   # periphery clone
sudo docker compose build && sudo docker compose up --abort-on-container-exit
```
