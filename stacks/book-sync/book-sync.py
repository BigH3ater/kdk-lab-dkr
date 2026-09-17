#!/usr/bin/env python3
"""book-sync: reconcile Chaptarr's ebook library onto the reMarkable.

Source of truth = the ebook library folder BOOKS_DIR (/mnt/media/books, managed by
Chaptarr via Prowlarr/SABnzbd). Add or remove a book in Chaptarr and it appears or
disappears on the tablet.
Each run makes the tablet's managed **"Kodiak Library"** folder match it:
  in folder, not on tablet (or changed)  -> push (create/replace the doc)
  on tablet (managed), not in folder      -> remove it (send + remove at will)
Only documents this service created are ever deleted -- their UUIDs are tracked in
state -- so books you added on the tablet directly are never touched.

Direct dropbear SSH into xochitl (the reliable path here; rmapi would need the
cloud). Opportunistic: if the Paper Pro is asleep, this run no-ops and the next
one reconciles. Pages Pushover on failure. Run periodically by a Komodo procedure.
"""
from __future__ import annotations
import os, sys, json, hashlib, subprocess, urllib.request, pathlib, datetime, time

BOOKS   = pathlib.Path(os.environ.get("BOOKS_DIR", "/books"))  # Chaptarr ebook library (/mnt/media/books)
STATE   = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
LIBNAME = os.environ.get("RM_LIB_FOLDER", "Kodiak Library")
RM_HOST = os.environ.get("RM_HOST", "10.1.30.245")
RM_USER = os.environ.get("RM_USER", "root")
RM_PW   = os.environ.get("RM_PW", "")
XO = "/home/root/.local/share/remarkable/xochitl"
EXTS = {".epub": "epub", ".pdf": "pdf"}
STATE.mkdir(parents=True, exist_ok=True)
ST = STATE/"book-sync.json"

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
    except Exception: return {"folder": None, "books": {}}
def save_state(s): ST.write_text(json.dumps(s, indent=2))
def md5(p):
    h=hashlib.md5()
    with open(p,"rb") as f:
        for b in iter(lambda: f.read(1<<20), b""): h.update(b)
    return h.hexdigest()

# ---- tablet ---------------------------------------------------------------
_SSH=["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
def _ssh(cmd, timeout=60): return subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd],capture_output=True,timeout=timeout)
def _scp(local,remote,timeout=180): subprocess.run(["sshpass","-p",RM_PW,"scp",*_SSH,str(local),f"{RM_USER}@{RM_HOST}:{remote}"],check=True,timeout=timeout)
def tablet_up():
    try: return _ssh("echo ok",timeout=15).stdout.strip()==b"ok"
    except Exception: return False
def new_uuid(): return _ssh("cat /proc/sys/kernel/random/uuid").stdout.decode().strip()
def exists(uuid): return _ssh(f"test -f {XO}/{uuid}.metadata && echo y").stdout.strip()==b"y"

def ensure_folder(state):
    fid=state.get("folder")
    if fid and exists(fid): return fid
    fid=new_uuid(); ms=str(int(time.time()*1000))
    meta=('{"visibleName":"%s","type":"CollectionType","parent":"","lastModified":"%s","version":0,'
          '"deleted":false,"pinned":false,"synced":false,"metadatamodified":true,"modified":true}') % (LIBNAME, ms)
    _ssh(f"printf '%s' '{meta}' > {XO}/{fid}.metadata; echo '{{}}' > {XO}/{fid}.content")
    state["folder"]=fid; log("created tablet folder",LIBNAME,fid[:8]); return fid

def push_book(local, fid, name, ext, uuid=None):
    uuid = uuid or new_uuid(); ms=str(int(time.time()*1000))
    _scp(local, f"{XO}/{uuid}.{ext}")
    meta=('{"visibleName":"%s","type":"DocumentType","parent":"%s","lastModified":"%s","version":0,'
          '"deleted":false,"pinned":false,"synced":false,"metadatamodified":true,"modified":true,'
          '"lastOpened":"%s","lastOpenedPage":0}') % (name.replace("'","").replace('"',''), fid, ms, ms)
    _ssh(f"printf '%s' '{meta}' > {XO}/{uuid}.metadata; echo '{{\"fileType\":\"{ext}\"}}' > {XO}/{uuid}.content")
    return uuid

def remove_book(uuid):
    # explicit paths only (no glob) -- delete the doc files + any annotation dir
    _ssh(f"rm -f {XO}/{uuid}.metadata {XO}/{uuid}.content {XO}/{uuid}.epub {XO}/{uuid}.pdf; rm -rf {XO}/{uuid} {XO}/{uuid}.thumbnails")

# ---- reconcile ------------------------------------------------------------
def main():
    if not BOOKS.exists():
        log(f"{BOOKS} missing; nothing to sync"); return
    if not tablet_up():
        log("tablet asleep; skipping (will reconcile next run)"); return
    state=load_state(); fid=ensure_folder(state)
    # desired set: relpath -> (abspath, md5, ext, name)
    want={}
    for p in sorted(BOOKS.rglob("*")):
        if p.is_file() and p.suffix.lower() in EXTS:
            rel=str(p.relative_to(BOOKS)); want[rel]=(p, md5(p), EXTS[p.suffix.lower()], p.stem)
    books=state.setdefault("books",{}); pushed=updated=removed=0; changed=False
    # add / update
    for rel,(p,h,ext,name) in want.items():
        cur=books.get(rel)
        if cur and cur.get("hash")==h and exists(cur.get("uuid","")):
            continue
        uuid=push_book(p, fid, name, ext, uuid=(cur or {}).get("uuid"))
        books[rel]={"uuid":uuid,"hash":h,"ext":ext,"name":name}
        changed=True
        if cur: updated+=1; log("updated",rel)
        else:   pushed+=1;  log("pushed",rel)
    # remove (managed books no longer in the folder)
    for rel in [r for r in books if r not in want]:
        remove_book(books[rel]["uuid"]); log("removed",rel); del books[rel]; removed+=1; changed=True
    save_state(state)
    if changed: _ssh("systemctl restart xochitl"); log("xochitl restarted")
    log(f"book-sync OK: {len(want)} desired, +{pushed} ~{updated} -{removed}")

if __name__=="__main__":
    try: main()
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"; log("FATAL:",msg); notify("book-sync failed",msg); raise
