#!/usr/bin/env python3
"""koreader-highlights: ingest KOReader highlights from the reMarkable into Obsidian.

KOReader stores annotations in a `.sdr` sidecar dir next to each book
(`<book>.sdr/metadata.<ext>.lua`, a Lua table). This pulls every sidecar under
RM_BOOKS_ROOT (/home/root/books) over dropbear SSH, parses the Lua, and writes one
Markdown note per book into the vault at HL_DIR (30-Books/Highlights). The vault is
kept in sync to all devices by the obsidian-headless stack.

Idempotent: a note is (re)written only when its sidecar changed (mtime tracked in
state). Opportunistic (tablet-awake); Pushover on failure. Run periodically by a
Komodo procedure. Reading + highlighting happen in KOReader; this is the ingest leg.
"""
from __future__ import annotations
import os, json, subprocess, urllib.request, urllib.parse, pathlib, datetime, shlex, re
from slpp import slpp as lua

VAULT    = pathlib.Path(os.environ.get("VAULT_DIR", "/vault"))
HL_DIR   = VAULT/os.environ.get("HL_SUBDIR", "30-Books/Highlights")
STATE    = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
RM_ROOT  = os.environ.get("RM_BOOKS_ROOT", "/home/root/books")
RM_HOST  = os.environ.get("RM_HOST", "10.1.30.245")
RM_USER  = os.environ.get("RM_USER", "root")
RM_PW    = os.environ.get("RM_PW", "")
STATE.mkdir(parents=True, exist_ok=True)
ST = STATE/"koreader-highlights.json"

def log(*a): print(datetime.datetime.now().strftime("%F %T"), *a, flush=True)
def notify(title, msg):
    tok, usr = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if not (tok and usr): return
    body=urllib.parse.urlencode({"token":tok,"user":usr,"title":title,"message":msg,"priority":1}).encode()
    try: urllib.request.urlopen("https://api.pushover.net/1/messages.json",data=body,timeout=10)
    except Exception: pass
def load_state():
    try: return json.loads(ST.read_text())
    except Exception: return {"sidecars": {}}
def save_state(s): ST.write_text(json.dumps(s, indent=2))
def safe(name):
    return (re.sub(r'[/:\\\x00-\x1f]', "-", name).strip() or "untitled")[:120]

# ---- tablet ---------------------------------------------------------------
_SSH=["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
def _ssh(cmd, timeout=60): return subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd],capture_output=True,timeout=timeout)
def tablet_up():
    try: return _ssh("echo ok",timeout=15).stdout.strip()==b"ok"
    except Exception: return False
def list_sidecars():
    # path<TAB>mtime for every KOReader metadata sidecar under RM_ROOT
    r=_ssh(f"find {shlex.quote(RM_ROOT)} -type f -name 'metadata.*.lua' -exec sh -c 'printf \"%s\\t%s\\n\" \"$1\" \"$(stat -c %Y \"$1\" 2>/dev/null || echo 0)\"' _ {{}} \\;")
    out=[]
    for line in r.stdout.decode(errors="replace").splitlines():
        if "\t" in line:
            p,_,m=line.rpartition("\t"); out.append((p, m.strip()))
    return out
def read_remote(path):
    return _ssh(f"cat {shlex.quote(path)}", timeout=60).stdout.decode(errors="replace")

# ---- parse + render -------------------------------------------------------
def parse(lua_text):
    body = lua_text.split("return", 1)[1] if "return" in lua_text else lua_text
    data = lua.decode(body)
    if not isinstance(data, dict): raise ValueError("not a table")
    props = data.get("doc_props") or {}
    title = props.get("title") or ""
    author = props.get("authors") or props.get("author") or ""
    anns = []
    a = data.get("annotations")
    if isinstance(a, dict): a = [a[k] for k in sorted(a)]
    if isinstance(a, list):
        for h in a:
            if isinstance(h, dict) and (h.get("text") or h.get("note")):
                anns.append({"text":h.get("text","") or "","note":h.get("note","") or "",
                             "chapter":h.get("chapter","") or "","page":h.get("pageno") or h.get("page") or "",
                             "dt":h.get("datetime","") or ""})
    else:  # older 'highlight' format: {page: {n: {...}}}
        hl = data.get("highlight") or {}
        if isinstance(hl, dict):
            for pg in sorted(hl, key=lambda x:(isinstance(x,int),x)):
                grp=hl[pg]; grp=[grp[k] for k in sorted(grp)] if isinstance(grp,dict) else grp
                for h in (grp or []):
                    if isinstance(h,dict) and h.get("text"):
                        anns.append({"text":h.get("text",""),"note":h.get("note","") or "",
                                     "chapter":h.get("chapter","") or "","page":pg,"dt":h.get("datetime","") or ""})
    return title, author, anns

def render_md(title, author, anns, src):
    now=datetime.datetime.now().strftime("%F %T")
    L=["---", f"title: {json.dumps(title)}", f"author: {json.dumps(author)}",
       "source: KOReader", f"updated: {now}", "tags: [reading/highlights]", "---", "",
       f"# {title or 'Untitled'}", ""]
    if author: L.append(f"*{author}*"); L.append("")
    L.append(f"> [!info] {len(anns)} highlight(s) ingested from KOReader"); L.append("")
    last=None
    for h in anns:
        head=h["chapter"] or (f"Page {h['page']}" if h["page"]!="" else "")
        if head and head!=last: L.append(f"## {head}"); L.append(""); last=head
        pg=f"  <sub>p.{h['page']}</sub>" if h["page"]!="" else ""
        txt=(h["text"] or "").strip().replace("\n"," ")
        if txt: L.append(f"> {txt}{pg}")
        if h["note"].strip(): L.append(f"> "); L.append(f"> 📝 {h['note'].strip()}")
        L.append("")
    L.append(f"<!-- source: {src} -->")
    return "\n".join(L)

# ---- main -----------------------------------------------------------------
def main():
    if not VAULT.exists(): log(f"{VAULT} missing (obsidian-headless not mounted?)"); return
    if not tablet_up(): log("tablet asleep; skipping"); return
    HL_DIR.mkdir(parents=True, exist_ok=True)
    state=load_state(); tracked=state.setdefault("sidecars",{})
    sidecars=list_sidecars(); log(f"{len(sidecars)} KOReader sidecar(s) on tablet")
    wrote=skipped=empty=0; failed=[]
    for path,mtime in sidecars:
        if tracked.get(path,{}).get("mtime")==mtime and tracked.get(path,{}).get("ok"):
            skipped+=1; continue
        try:
            title,author,anns=parse(read_remote(path))
        except Exception as e:
            failed.append(f"{path}: {e}"); log("parse FAILED",path,e); continue
        prev=tracked.get(path,{}).get("dest")
        book=pathlib.PurePath(path).parent.name[:-4]   # "<book>.sdr" -> "<book>"
        if not anns:
            if prev: (HL_DIR/prev).unlink(missing_ok=True); log("removed orphan (highlights cleared)",prev)
            tracked[path]={"mtime":mtime,"ok":True,"dest":None}; empty+=1; continue
        title=title or book
        # disambiguate same-titled books (append author, else the book filename)
        disamb=safe(author) if author else safe(book)
        dest=HL_DIR/(f"{safe(title)} - {disamb}.md" if disamb and disamb!=safe(title) else f"{safe(title)}.md")
        if prev and prev!=dest.name: (HL_DIR/prev).unlink(missing_ok=True)   # renamed -> drop old
        dest.write_text(render_md(title,author,anns,path), encoding="utf-8")
        tracked[path]={"mtime":mtime,"ok":True,"dest":dest.name}; wrote+=1; log("wrote",dest.name,f"({len(anns)} hl)")
    save_state(state)
    log(f"koreader-highlights OK: {wrote} written, {skipped} unchanged, {empty} no-highlights, {len(failed)} failed")
    if failed: notify(f"koreader-highlights: {len(failed)} failed","\n".join(failed[:8]))

if __name__=="__main__":
    try: main()
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"; log("FATAL:",msg); notify("koreader-highlights failed",msg); raise
