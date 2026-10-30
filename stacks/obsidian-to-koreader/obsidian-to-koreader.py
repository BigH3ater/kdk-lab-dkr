#!/usr/bin/env python3
"""obsidian-to-koreader: render Obsidian notes to PDF and put them on the tablet.

Two modes, in one one-shot run:

1. Request-driven ("Vault channel", the owner's "Send to reMarkable"): the Obsidian
   plugin drops a small JSON request into VAULT/<QUEUE_SUBDIR>/queue/. Because the lab
   mirror of the vault receives the file through the existing headless Obsidian Sync,
   the plugin needs no network access, endpoint or token. Each request names a SCOPE
   (a single note, a folder, or the whole vault), whether to render per-note or one
   combined PDF, and an optional target subfolder under RM_DEST. The job renders, pushes
   the PDFs to KOReader over SSH, then moves the request to <QUEUE_SUBDIR>/done/ with a
   status line (which the plugin reads back through the same sync to show the last
   result). If the tablet is asleep the request is LEFT in queue/ and retried next run.

2. Scheduled whole-section sync (the default, unchanged): when the queue is empty, every
   run reconciles each top-level vault section (00.Inbox, 10.Homelab, ...) to one PDF per
   section under RM_DEST, exactly as this stack has always done (rebuild changed sections,
   remove PDFs for deleted sections). So nothing regresses when the plugin is not in use.

Transport is opportunistic: SSH to the tablet's tailnet address first (remarkable-pp),
then the LAN address; if neither answers the tablet is asleep and the run no-ops (queue
requests stay queued). Read-only on vault content; the ONLY path it writes in the vault
is <QUEUE_SUBDIR>/ (a nested read-write mount), to move a processed request to done/.
Pushover on failure. Run periodically by a Komodo procedure.
"""
from __future__ import annotations
import os, json, subprocess, urllib.request, urllib.parse, pathlib, datetime, shlex, re, hashlib

VAULT   = pathlib.Path(os.environ.get("VAULT_DIR", "/vault"))
STATE   = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
RM_DEST = os.environ.get("RM_DEST", "/home/root/books/Homelab")
RM_USER = os.environ.get("RM_USER", "root")
RM_PW   = os.environ.get("RM_PW", "")
MAX_MB  = float(os.environ.get("MAX_PDF_MB", "40"))
ENGINE  = os.environ.get("PANDOC_ENGINE", "weasyprint")   # Phase 1 renderer decision
# only these top-level sections (regex) in the scheduled default run; default = numbered domains
SECTION_RE = re.compile(os.environ.get("SECTION_REGEX", r"^\d\d[-.]"))
# Tablet addresses, tried in order: tailnet first (reachable off-LAN), then LAN. A single
# RM_HOST (legacy) overrides both when set.
_single = os.environ.get("RM_HOST", "").strip()
RM_HOSTS = [_single] if _single else [
    os.environ.get("RM_HOST_TAILNET", "remarkable-pp").strip(),
    os.environ.get("RM_HOST_LAN", "10.1.30.245").strip(),
]
RM_HOSTS = [h for h in RM_HOSTS if h]
# request queue lives INSIDE the vault so the plugin reaches it through Obsidian Sync
QUEUE_SUB = os.environ.get("QUEUE_SUBDIR", "_remarkable")
QDIR   = VAULT/QUEUE_SUB/"queue"
DONE   = VAULT/QUEUE_SUB/"done"

STATE.mkdir(parents=True, exist_ok=True)
BUILD = STATE/"build"; BUILD.mkdir(exist_ok=True)
ST = STATE/"obsidian-to-koreader.json"
IMG_EXT = {".png",".jpg",".jpeg",".gif",".svg",".webp"}

def log(*a): print(datetime.datetime.now().strftime("%F %T"), *a, flush=True)
def now_iso(): return datetime.datetime.now().isoformat(timespec="seconds")
def notify(title,msg):
    tok,usr=os.environ.get("PUSHOVER_TOKEN"),os.environ.get("PUSHOVER_USER")
    if not (tok and usr): return
    body=urllib.parse.urlencode({"token":tok,"user":usr,"title":title,"message":msg,"priority":1}).encode()
    try: urllib.request.urlopen("https://api.pushover.net/1/messages.json",data=body,timeout=10)
    except Exception: pass
def load_state():
    try: return json.loads(ST.read_text())
    except Exception: return {"sections":{}}
def save_state(s): ST.write_text(json.dumps(s,indent=2))

# ---- markdown preprocessing (Obsidian -> plain markdown) ------------------
FM = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.S)       # tolerate CRLF frontmatter
DV = re.compile(r"```+dataview[a-z]*\r?\n.*?\r?\n```+", re.S)  # Dataview: dynamic, headless-blind
CALLOUT = re.compile(r"^>\s*\[!([A-Za-z-]+)\]([+-]?)\s*(.*)$")

def _img_index():
    """basename -> absolute path, for resolving Obsidian image embeds vault-wide
    (Obsidian resolves ![[name.png]] by name, not by path)."""
    idx={}
    for p in sorted(VAULT.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMG_EXT:
            idx.setdefault(p.name, p)        # first (shortest-sorted) wins on collision
    return idx
_IMG = None

def clean_md(text):
    global _IMG
    if _IMG is None: _IMG = _img_index()
    text = FM.sub("", text, count=1)                     # strip YAML frontmatter
    text = re.sub(r"%%.*?%%", "", text, flags=re.S)      # strip Obsidian comments
    text = DV.sub("*[Dataview query — renders only in Obsidian]*", text)
    def _embed(m):
        tgt = m.group(1).split("|")[0].strip()           # drop display-size "|300"
        name = pathlib.Path(tgt).name
        if pathlib.Path(tgt).suffix.lower() in IMG_EXT:
            hit = _IMG.get(name)
            return f"![]({hit.as_posix()})" if hit else ""  # resolved image, else drop
        return ""                                        # embed of another note -> drop
    text = re.sub(r"!\[\[([^\]]+)\]\]", _embed, text)    # image embeds -> md image; note embeds dropped
    out=[]                                               # callout header -> bold label (renders as blockquote)
    for ln in text.splitlines():
        m=CALLOUT.match(ln)
        if m:
            typ=m.group(1).upper(); title=m.group(3).strip()
            out.append(f"> **{typ}" + (f" — {title}" if title else "") + "**")
        else:
            out.append(ln)
    text="\n".join(out)
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)  # [[a|b]] -> b
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)      # [[a]] -> a
    return text

def note_markdown(md_path):
    rel = md_path.relative_to(VAULT).as_posix()[:-3]
    return f"\n\n# {rel}\n\n" + clean_md(md_path.read_text(encoding="utf-8",errors="replace"))

def tree_markdown(root):
    parts=[]
    for md in sorted(root.rglob("*.md")):
        parts.append(note_markdown(md))
    return "".join(parts)

def render_pdf(name, md_text, out_pdf):
    BUILD.mkdir(parents=True, exist_ok=True)   # writable scratch (uid 1000 owns /state)
    src=BUILD/f"{name}.md"; src.write_text(md_text, encoding="utf-8")
    cmd=["pandoc", str(src), "-o", str(out_pdf), f"--pdf-engine={ENGINE}",
         "--metadata", f"title={name}", "-V", "papersize=a5", "--resource-path", str(VAULT)]
    # pandoc writes its temp files in the CWD; the image WORKDIR (/app) is root-owned and
    # the container runs as uid 1000, so run from the writable build dir and point TMPDIR at
    # /tmp (openTempFile would otherwise fail: permission denied). All cmd paths are absolute.
    env={**os.environ, "TMPDIR": "/tmp"}
    r=subprocess.run(cmd, capture_output=True, timeout=1200, cwd=str(BUILD), env=env)
    if r.returncode!=0 or not out_pdf.exists() or out_pdf.stat().st_size==0:
        raise RuntimeError(f"pandoc failed: {r.stderr.decode(errors='replace')[-400:]}")

# ---- tablet (tailnet first, LAN fallback) ---------------------------------
_SSH=["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
RM_HOST=None   # resolved by pick_host()
def _ssh(cmd,timeout=60): return subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd],capture_output=True,timeout=timeout)
# modern scp = SFTP: pass the remote path raw (no shell-quote); spaces are fine.
def _scp(local,remote,timeout=180): subprocess.run(["sshpass","-p",RM_PW,"scp",*_SSH,str(local),f"{RM_USER}@{RM_HOST}:{remote}"],check=True,timeout=timeout)
def pick_host():
    """Set RM_HOST to the first address that answers SSH (tailnet, then LAN).
    ICMP is filtered on the tablet, so this probes with SSH, not ping."""
    global RM_HOST
    for h in RM_HOSTS:
        RM_HOST=h
        try:
            if _ssh("echo ok",timeout=15).stdout.strip()==b"ok":
                log("tablet reachable at",h); return True
        except Exception: pass
    RM_HOST=None; return False

def _safe_name(s): return re.sub(r"[^A-Za-z0-9._-]+","-",s).strip("-") or "vault"

# ---- request queue (the Vault channel) ------------------------------------
def _resolve_in_vault(relpath):
    """Resolve a vault-relative path, refusing anything that escapes the vault."""
    p=(VAULT/relpath).resolve()
    if not str(p).startswith(str(VAULT.resolve())+os.sep) and p!=VAULT.resolve():
        raise ValueError(f"path escapes vault: {relpath}")
    return p

def _jobs_for(req):
    """Return [(pdf_stem, markdown)] for a request, honouring scope + mode."""
    scope=req.get("scope","note"); mode=req.get("mode","combined")
    if scope=="vault":
        secs=[d for d in sorted(VAULT.iterdir()) if d.is_dir() and SECTION_RE.match(d.name)]
        if mode=="combined":
            return [("Vault", "".join(tree_markdown(s) for s in secs))]
        return [(s.name, tree_markdown(s)) for s in secs]          # per-note == per-section
    path=(req.get("path") or "").strip().lstrip("/")
    if not path: raise ValueError("scope needs a path")
    target=_resolve_in_vault(path)
    if scope=="note":
        if target.suffix.lower()!=".md" or not target.is_file(): raise ValueError(f"not a note: {path}")
        return [(target.stem, note_markdown(target))]
    if scope=="folder":
        if not target.is_dir(): raise ValueError(f"not a folder: {path}")
        if mode=="per-note":
            return [(md.stem, note_markdown(md)) for md in sorted(target.rglob("*.md"))]
        return [(target.name or "Vault", tree_markdown(target))]   # combined
    raise ValueError(f"unknown scope: {scope}")

def _deliver(stem, md_text, dest_dir):
    if not md_text.strip(): return None, "empty (nothing to render)"
    out=BUILD/f"{_safe_name(stem)}.pdf"
    render_pdf(_safe_name(stem), md_text, out)
    mb=out.stat().st_size/1e6
    if mb>MAX_MB: return None, f"{mb:.1f}MB > {MAX_MB}MB guard, skipped"
    _ssh(f"mkdir -p {shlex.quote(dest_dir)}")
    remote=f"{dest_dir}/{_safe_name(stem)}.pdf"
    _scp(out, remote); return remote, f"{mb:.1f}MB"

def process_queue():
    """Process every queued request. Returns the number of requests handled, or -1 if
    the tablet is asleep (requests left queued for the next run)."""
    reqs=sorted(QDIR.glob("*.json")) if QDIR.exists() else []
    if not reqs: return 0
    if not pick_host():
        log(f"{len(reqs)} request(s) queued but tablet asleep; left in queue/ for next run"); return -1
    DONE.mkdir(parents=True, exist_ok=True)
    handled=0; failed=[]
    for rf in reqs:
        rid=rf.stem
        try: req=json.loads(rf.read_text())
        except Exception as e:
            req={}; status="failed"; detail=f"bad JSON: {e}"; delivered=[]
        else:
            dest=RM_DEST
            sub=(req.get("target_subdir") or "").strip().strip("/")
            if sub: dest=f"{RM_DEST}/"+"/".join(_safe_name(p) for p in sub.split("/"))
            delivered=[]; errs=[]
            try:
                jobs=_jobs_for(req)
                for stem,md in jobs:
                    try:
                        remote,info=_deliver(stem,md,dest)
                        if remote: delivered.append(remote); log("delivered",remote,f"({info})")
                        else: errs.append(f"{stem}: {info}")
                    except Exception as e: errs.append(f"{stem}: {e}")
                if not jobs: errs.append("scope matched no notes")
            except Exception as e: errs.append(str(e))
            if delivered and not errs: status="ok"; detail=f"delivered {len(delivered)} PDF(s)"
            elif delivered: status="partial"; detail="; ".join(errs)[:400]
            else: status="failed"; detail="; ".join(errs)[:400] or "nothing delivered"
        rec=dict(req); rec.update({"id":req.get("id",rid),"status":status,"status_detail":detail,
                  "delivered":delivered,"host":RM_HOST,"processed_at":now_iso()})
        (DONE/f"{rid}.json").write_text(json.dumps(rec,indent=2))
        rf.unlink()                                 # move queue -> done
        handled+=1; log(f"request {rid}: {status} ({detail})")
        if status!="ok": failed.append(f"{rid}: {detail}")
    if failed: notify(f"vault-channel: {len(failed)} request(s) failed","\n".join(failed[:8]))
    return handled

# ---- scheduled whole-section reconcile (the default) ----------------------
def run_sections():
    if not pick_host(): log("tablet asleep; skipping scheduled section sync"); return
    _ssh(f"mkdir -p {shlex.quote(RM_DEST)}")
    state=load_state(); tracked=state.setdefault("sections",{})
    sections=[d for d in sorted(VAULT.iterdir()) if d.is_dir() and SECTION_RE.match(d.name)]
    log(f"{len(sections)} section(s): {[s.name for s in sections]}")
    built=skipped=removed=0; failed=[]; seen=set()
    for sec in sections:
        md=tree_markdown(sec)
        if not md.strip(): continue
        seen.add(sec.name)
        h=hashlib.md5(md.encode()).hexdigest()
        if tracked.get(sec.name,{}).get("hash")==h:
            skipped+=1; continue
        out=BUILD/f"{sec.name}.pdf"
        try: render_pdf(sec.name, md, out)
        except Exception as e: failed.append(f"{sec.name}: {e}"); log("render FAILED",sec.name,e); continue
        mb=out.stat().st_size/1e6
        if mb>MAX_MB:
            failed.append(f"{sec.name}: {mb:.1f}MB > {MAX_MB}MB guard"); log("too big, skip",sec.name,f"{mb:.1f}MB"); continue
        _scp(out, f"{RM_DEST}/{sec.name}.pdf")
        tracked[sec.name]={"hash":h}; built+=1; log("delivered",f"{sec.name}.pdf",f"({mb:.1f}MB)")
    for name in [n for n in list(tracked) if n not in seen]:
        _ssh(f"rm -f {shlex.quote(RM_DEST+'/'+name+'.pdf')}"); log("removed",name+".pdf"); del tracked[name]; removed+=1
    save_state(state)
    log(f"section sync OK: +{built} ~{skipped}unchanged -{removed}, !{len(failed)} failed")
    if failed: notify(f"obsidian-to-koreader: {len(failed)} failed","\n".join(failed[:8]))

# ---- main -----------------------------------------------------------------
def main():
    if not VAULT.exists(): log(f"{VAULT} missing (obsidian-headless not mounted?)"); return
    handled=process_queue()
    if handled>0:
        log(f"vault channel: {handled} request(s) handled")
        return                      # a plugin-driven run; skip the scheduled dump
    if handled==0:                  # queue empty -> default behaviour, unchanged
        run_sections()
    # handled==-1: tablet asleep, requests left queued; do nothing else this run

if __name__=="__main__":
    try: main()
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"; log("FATAL:",msg); notify("obsidian-to-koreader failed",msg); raise
