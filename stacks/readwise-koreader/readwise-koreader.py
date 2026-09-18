#!/usr/bin/env python3
"""readwise-koreader: mirror Readwise Reader articles onto the reMarkable for KOReader.

Fetches documents from the Readwise Reader API (v3 /list/) in the configured locations,
keeps only those carrying TAG (default 'remarkable'), renders each article's HTML to
EPUB (Calibre ebook-convert), and copies it as a plain file into RM_BOOKS_DIR
(/home/root/books/Readwise) over dropbear SSH — KOReader reads plain files, not
xochitl's store. Reconciles: delivers newly-tagged docs, removes ones no longer tagged
(only files it created, tracked in state). Highlights made in KOReader land in `.sdr`
sidecars next to each file and are ingested to Obsidian by the koreader-highlights job.

"Use Chaptarr where possible": Chaptarr can't fetch web articles, so this small fetcher
handles Readwise, but delivery reuses the exact KOReader file-push pattern as book-sync.
Opportunistic (tablet-awake); Pushover on failure. Run periodically by a Komodo procedure.
"""
from __future__ import annotations
import os, json, subprocess, urllib.request, urllib.error, urllib.parse, pathlib, datetime, shlex, re, time

TOKEN_FILE = os.environ.get("READWISE_TOKEN_FILE", "/secrets/readwise-token")
TOKEN      = os.environ.get("READWISE_TOKEN", "")
LOCATIONS  = [s.strip() for s in os.environ.get("LOCATIONS", "new,later,shortlist").split(",") if s.strip()]
TAG        = os.environ.get("TAG", "remarkable").lower()
CATEGORIES = {s.strip() for s in os.environ.get("CATEGORIES", "article,email,rss,pdf,epub").split(",") if s.strip()}
STATE      = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
RM_BOOKS   = os.environ.get("RM_BOOKS_DIR", "/home/root/books/Readwise")
RM_HOST    = os.environ.get("RM_HOST", "10.1.30.245")
RM_USER    = os.environ.get("RM_USER", "root")
RM_PW      = os.environ.get("RM_PW", "")
API        = "https://readwise.io/api/v3/list/"
THROTTLE   = float(os.environ.get("THROTTLE_SECONDS", "3.5"))   # Reader LIST is 20 req/min
STATE.mkdir(parents=True, exist_ok=True)
TMP = STATE/"tmp"; TMP.mkdir(exist_ok=True)
ST  = STATE/"readwise-koreader.json"

def log(*a): print(datetime.datetime.now().strftime("%F %T"), *a, flush=True)
def notify(title, msg):
    tok, usr = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if not (tok and usr): return
    body=urllib.parse.urlencode({"token":tok,"user":usr,"title":title,"message":msg,"priority":1}).encode()
    try: urllib.request.urlopen("https://api.pushover.net/1/messages.json",data=body,timeout=10)
    except Exception: pass
def load_state():
    try: return json.loads(ST.read_text())
    except Exception: return {"docs": {}}
def save_state(s): ST.write_text(json.dumps(s, indent=2))
def token():
    if TOKEN: return TOKEN
    try: return pathlib.Path(TOKEN_FILE).read_text().strip()
    except Exception: raise SystemExit("no Readwise token (READWISE_TOKEN or READWISE_TOKEN_FILE)")

def safe(name):
    name = re.sub(r"[/:\\\x00-\x1f]", "-", name).strip() or "untitled"
    return name[:120]

# ---- Readwise Reader API --------------------------------------------------
def _api_get(url, tok):
    for _ in range(6):
        req=urllib.request.Request(url, headers={"Authorization":f"Token {tok}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code==429:
                wait=int(e.headers.get("Retry-After","60") or "60")
                log(f"readwise 429; sleeping {wait}s"); time.sleep(wait); continue
            raise
    raise RuntimeError("readwise API: too many 429s")

def fetch_docs():
    tok=token(); out={}
    for loc in LOCATIONS:
        cursor=None
        while True:
            # server-side tag filter (Reader v3 supports it) + client-side re-check below
            q=[("location",loc),("withHtmlContent","true"),("tag",TAG)]
            if cursor: q.append(("pageCursor",cursor))
            data=_api_get(API+"?"+urllib.parse.urlencode(q), tok)
            for d in data.get("results", []):
                tags=d.get("tags") or {}
                tagset={str(t).lower() for t in (tags.keys() if isinstance(tags,dict) else tags)}
                if TAG not in tagset: continue
                if (d.get("category") or "article") not in CATEGORIES: continue
                out[d["id"]]=d
            cursor=data.get("nextPageCursor")
            if not cursor: break
            time.sleep(THROTTLE)   # stay under the 20 req/min LIST limit
    return out

# ---- EPUB render ----------------------------------------------------------
def to_epub(doc):
    title=doc.get("title") or "Untitled"; author=doc.get("author") or doc.get("site_name") or "Readwise"
    html=doc.get("html_content") or ""
    if not html.strip():
        # no article body (e.g. a bare pdf/epub doc with only a source_url) -> skip for now
        return None
    hp=TMP/f"{doc['id']}.html"; ep=TMP/f"{doc['id']}.epub"
    hp.write_text(f"<html><head><meta charset='utf-8'><title>{title}</title></head><body>{html}</body></html>", encoding="utf-8")
    r=subprocess.run(["ebook-convert", str(hp), str(ep),
                      "--title", title, "--authors", author,
                      "--no-default-epub-cover"], capture_output=True, timeout=600)
    hp.unlink(missing_ok=True)
    if r.returncode!=0 or not ep.exists() or ep.stat().st_size==0:
        ep.unlink(missing_ok=True)
        raise RuntimeError(f"ebook-convert failed: {r.stderr.decode(errors='replace')[-300:]}")
    return ep

# ---- tablet (plain files for KOReader) ------------------------------------
_SSH=["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
def _ssh(cmd, timeout=60): return subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd],capture_output=True,timeout=timeout)
# modern scp = SFTP protocol (no remote shell): remote path is literal, do NOT shell-quote
# it (quotes would become part of the name); spaces are fine. _ssh cmds still use shlex.quote.
def _scp(local,remote,timeout=180): subprocess.run(["sshpass","-p",RM_PW,"scp",*_SSH,str(local),f"{RM_USER}@{RM_HOST}:{remote}"],check=True,timeout=timeout)
def tablet_up():
    try: return _ssh("echo ok",timeout=15).stdout.strip()==b"ok"
    except Exception: return False
def r_exists(path): return _ssh(f"test -f {shlex.quote(path)} && echo y").stdout.strip()==b"y"

def deliver(ep, dest_rel):
    _ssh(f"mkdir -p {shlex.quote(RM_BOOKS)}")
    _scp(str(ep), f"{RM_BOOKS}/{dest_rel}")
def remove(dest_rel):
    _ssh(f"rm -f {shlex.quote(RM_BOOKS + '/' + dest_rel)}")   # leave .sdr so un-ingested highlights survive

# ---- reconcile ------------------------------------------------------------
def main():
    if not tablet_up():
        log("tablet asleep; skipping (will reconcile next run)"); return
    docs=fetch_docs(); log(f"{len(docs)} tagged '{TAG}' doc(s) across {LOCATIONS}")
    state=load_state(); tracked=state.setdefault("docs",{})
    delivered=removed=0; failed=[]; changed=False; seen=set()
    for did,doc in docs.items():
        seen.add(did)
        stamp=str(doc.get("updated_at") or doc.get("last_moved_at") or "")
        dest=f"{safe(doc.get('title') or did)}-{str(did)[:8]}.epub"   # id suffix avoids title collisions
        cur=tracked.get(did)
        if cur and cur.get("stamp")==stamp and cur.get("dest")==dest and r_exists(f"{RM_BOOKS}/{dest}"):
            continue
        try:
            ep=to_epub(doc)
            if ep is None:
                log("skip (no html body):", doc.get("title")); continue
        except Exception as e:
            failed.append(f"{doc.get('title')}: {e}"); log("convert FAILED", doc.get("title"), e); continue
        if cur and cur.get("dest") and cur["dest"]!=dest: remove(cur["dest"])
        deliver(ep, dest); ep.unlink(missing_ok=True)
        tracked[did]={"dest":dest,"stamp":stamp}; delivered+=1; changed=True; log("delivered", dest)
    # remove docs no longer tagged/returned
    for did in [d for d in list(tracked) if d not in seen]:
        remove(tracked[did]["dest"]); log("removed", tracked[did]["dest"]); del tracked[did]; removed+=1; changed=True
    save_state(state)
    log(f"readwise-koreader OK: {len(docs)} tagged, +{delivered} -{removed}, !{len(failed)} failed")
    if failed: notify(f"readwise-koreader: {len(failed)} failed", "\n".join(failed[:8]))

if __name__=="__main__":
    try: main()
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"; log("FATAL:",msg); notify("readwise-koreader failed",msg); raise
