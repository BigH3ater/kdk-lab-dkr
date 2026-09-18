#!/usr/bin/env python3
"""book-sync: reconcile Chaptarr's ebook library onto the reMarkable for KOReader.

Source of truth = the ebook library folder BOOKS_DIR (/mnt/media/books/ebook, managed
by Chaptarr via Prowlarr/SABnzbd). Add or remove a book in Chaptarr and it appears or
disappears on the tablet.

Delivery = **plain files** into the KOReader library dir RM_BOOKS_DIR
(/home/root/books/Library, on the persistent /home partition). KOReader browses a
folder of real files — it does NOT read xochitl's document store — so we copy the
book file (mirroring the Chaptarr Author/Title/ structure) rather than injecting a
document into xochitl. Highlights made in KOReader land in `.sdr` sidecars next to
each book and are ingested to Obsidian by the koreader-highlights job (separate).

Each run makes RM_BOOKS_DIR match the library:
  in library, not on tablet (or changed) -> copy it
  on tablet (managed), not in library     -> delete it (send + remove at will)
Only files this service created are ever deleted -- their dest paths are tracked in
state -- so books you added on the tablet directly are never touched.

Non-EPUB/PDF ebook formats are converted to EPUB (Calibre) first. Direct dropbear SSH
(reachable over the LAN / Tailscale `remarkable-pp`). Opportunistic: if the Paper Pro
is asleep, this run no-ops and the next one reconciles. Pages Pushover on failure.
Run periodically by a Komodo procedure.
"""
from __future__ import annotations
import os, sys, json, hashlib, subprocess, urllib.request, pathlib, datetime, shlex

BOOKS    = pathlib.Path(os.environ.get("BOOKS_DIR", "/books"))  # Chaptarr ebook library
STATE    = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
RM_BOOKS = os.environ.get("RM_BOOKS_DIR", "/home/root/books/Library")  # KOReader library dir
RM_HOST  = os.environ.get("RM_HOST", "10.1.30.245")
RM_USER  = os.environ.get("RM_USER", "root")
RM_PW    = os.environ.get("RM_PW", "")
# KOReader reads many formats; EPUB + PDF pass through, the rest convert to EPUB.
NATIVE  = {".epub", ".pdf"}
CONVERT = {".azw3", ".azw", ".mobi", ".fb2", ".lit", ".pdb", ".prc"}
STATE.mkdir(parents=True, exist_ok=True)
CONVERTED = STATE/"converted"; CONVERTED.mkdir(exist_ok=True)
ST = STATE/"book-sync.json"

def convert_to_epub(src, h):
    out = CONVERTED/f"{h}.epub"
    if out.exists() and out.stat().st_size > 0:
        return out
    r = subprocess.run(["ebook-convert", str(src), str(out)], capture_output=True, timeout=900)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"ebook-convert failed ({src.suffix}): {r.stderr.decode(errors='replace')[-300:]}")
    return out

def log(*a): print(datetime.datetime.now().strftime("%F %T"), *a, flush=True)
def notify(title, msg):
    tok, usr = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if not (tok and usr): return
    import urllib.parse
    body=urllib.parse.urlencode({"token":tok,"user":usr,"title":title,"message":msg,"priority":1}).encode()
    try: urllib.request.urlopen("https://api.pushover.net/1/messages.json",data=body,timeout=10)
    except Exception: pass
def load_state():
    try: return json.loads(ST.read_text())
    except Exception: return {"books": {}}
def save_state(s): ST.write_text(json.dumps(s, indent=2))
def md5(p):
    h=hashlib.md5()
    with open(p,"rb") as f:
        for b in iter(lambda: f.read(1<<20), b""): h.update(b)
    return h.hexdigest()

# ---- tablet ---------------------------------------------------------------
_SSH=["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
def _ssh(cmd, timeout=60): return subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd],capture_output=True,timeout=timeout)
def _scp(local,remote,timeout=180): subprocess.run(["sshpass","-p",RM_PW,"scp",*_SSH,str(local),f"{RM_USER}@{RM_HOST}:{shlex.quote(remote)}"],check=True,timeout=timeout)
def tablet_up():
    try: return _ssh("echo ok",timeout=15).stdout.strip()==b"ok"
    except Exception: return False
def r_exists(path):
    return _ssh(f"test -f {shlex.quote(path)} && echo y").stdout.strip()==b"y"

def push_book(local, dest_rel):
    dest = f"{RM_BOOKS}/{dest_rel}"
    parent = os.path.dirname(dest)
    _ssh(f"mkdir -p {shlex.quote(parent)}")
    _scp(local, dest)

def remove_book(dest_rel):
    # explicit path only (no glob). Leave the .sdr sidecar so un-ingested highlights survive.
    _ssh(f"rm -f {shlex.quote(RM_BOOKS + '/' + dest_rel)}")

# ---- reconcile ------------------------------------------------------------
def main():
    if not BOOKS.exists():
        log(f"{BOOKS} missing; nothing to sync"); return
    if not tablet_up():
        log("tablet asleep; skipping (will reconcile next run)"); return
    state=load_state()
    _ssh(f"mkdir -p {shlex.quote(RM_BOOKS)}")
    # desired set: src_relpath -> (abspath, md5, dest_relpath, needs_convert)
    want={}
    for p in sorted(BOOKS.rglob("*")):
        if not p.is_file(): continue
        sfx=p.suffix.lower()
        rel=str(p.relative_to(BOOKS))
        if sfx in NATIVE:    want[rel]=(p, md5(p), rel, False)
        elif sfx in CONVERT: want[rel]=(p, md5(p), str(pathlib.PurePath(rel).with_suffix(".epub")), True)
    books=state.setdefault("books",{}); pushed=updated=removed=0; changed=False; failed=[]
    # add / update
    for rel,(p,h,dest_rel,conv) in want.items():
        cur=books.get(rel)
        if cur and cur.get("hash")==h and r_exists(f"{RM_BOOKS}/{cur.get('dest','')}"):
            continue
        try:
            src = convert_to_epub(p, h) if conv else p
        except Exception as e:
            failed.append(f"{rel}: {e}"); log("convert FAILED", rel, e); continue
        # if the dest path changed (e.g. re-convert), drop the old file
        if cur and cur.get("dest") and cur["dest"]!=dest_rel:
            remove_book(cur["dest"])
        push_book(src, dest_rel)
        books[rel]={"dest":dest_rel,"hash":h}
        changed=True
        if cur: updated+=1; log("updated",dest_rel,f"({p.suffix}->epub)" if conv else "")
        else:   pushed+=1;  log("pushed",dest_rel,f"({p.suffix}->epub)" if conv else "")
    # remove (managed books no longer in the library)
    for rel in [r for r in books if r not in want]:
        remove_book(books[rel]["dest"]); log("removed",books[rel]["dest"]); del books[rel]; removed+=1; changed=True
    save_state(state)
    log(f"book-sync OK: {len(want)} desired, +{pushed} ~{updated} -{removed}, !{len(failed)} failed")
    if failed:
        notify(f"book-sync: {len(failed)} conversion(s) failed", "\n".join(failed[:8]))

if __name__=="__main__":
    try: main()
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"; log("FATAL:",msg); notify("book-sync failed",msg); raise
