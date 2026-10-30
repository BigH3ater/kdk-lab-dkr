#!/usr/bin/env python3
"""obsidian-to-koreader: render Obsidian notes to PDF and put them on the tablet.

Runs in one of two MODEs (env MODE), sharing the SAME request-processing and
rendering code:

* MODE=watch  -- a long-running watcher (compose service, restart: unless-stopped).
  It polls VAULT/<QUEUE_SUBDIR>/queue/ every POLL_SECONDS and processes a request
  within seconds of its arrival, so a "Send to reMarkable" tap lands on the tablet
  almost immediately instead of waiting for the 6-hourly run. When the tablet is
  asleep it leaves the request queued and backs off (probing at most every
  SLEEP_BACKOFF_MAX seconds, not every poll). It writes a /state heartbeat for the
  compose healthcheck and shares a /state lock with the one-shot so the same request
  is never processed twice.

* MODE=oneshot (default) -- the scheduled Komodo one-shot (every 6 h). It first
  DRAINS the queue as a fallback (in case the watcher is down), then runs the
  whole-tree reconcile:
    - SYNC_FOLDERS (default "20.Work"): mirror every note per-note into the tablet
      tree, re-rendering only notes whose content hash changed and removing tablet
      PDFs whose source note disappeared (the .sdr highlight sidecar is left in place).
    - every other top-level section (SECTION_REGEX) -> one combined PDF, as before.

Both modes deliver into a folder tree that MIRRORS the vault: a note at
`1. Projects/foo/Bench Layout.md` is delivered to
`RM_DEST/1. Projects/foo/Bench Layout.pdf` (real names, with spaces, kept so
KOReader shows them as-is). Combined PDFs go inside the folder they cover.

Transport is opportunistic SSH: tailnet address first (remarkable-pp), then LAN; if
neither answers the tablet is asleep and the request stays queued. Files are copied
with `cat`-over-SSH (one shell-quoted remote arg) rather than scp, so remote paths
with spaces/apostrophes/unicode are handled correctly regardless of the tablet's scp
protocol. Read-only on vault content; the ONLY path written in the vault is
<QUEUE_SUBDIR>/ (a nested read-write mount), to move a processed request to done/.
Mermaid code blocks are rendered to SVG at build time via the internal Kroki service
(KROKI_URL); if Kroki is unreachable the diagram falls back to its source text.
Pushover on failure.
"""
from __future__ import annotations
import os, json, subprocess, urllib.request, urllib.parse, pathlib, datetime, shlex, re, hashlib
import fcntl, time, tempfile, shutil

VAULT   = pathlib.Path(os.environ.get("VAULT_DIR", "/vault"))
STATE   = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
RM_DEST = os.environ.get("RM_DEST", "/home/root/books/kodiak-codex")
# One-time: the vault folder was renamed Homelab -> kodiak-codex (the vault's name).
# On the first reconcile, if RM_DEST_OLD still exists and RM_DEST does not, it is
# `mv`d over (keeping any .sdr sidecars); never deleted. Set empty to disable.
RM_DEST_OLD = os.environ.get("RM_DEST_OLD", "/home/root/books/Homelab")
RM_USER = os.environ.get("RM_USER", "root")
RM_PW   = os.environ.get("RM_PW", "")
MAX_MB  = float(os.environ.get("MAX_PDF_MB", "40"))
ENGINE  = os.environ.get("PANDOC_ENGINE", "weasyprint")   # Phase 1 renderer decision
# Render for the DEVICE screen, not paper. The Paper Pro panel is 1620x2160 px at ~229 ppi
# = ~180x240 mm (3:4); an A4 PDF shows each page as two half-screens in KOReader. Make the
# page size env-configurable so a reMarkable 2 or true paper output can be chosen later.
PAGE_SIZE = os.environ.get("PDF_PAGE_SIZE", "180mm 240mm")
# only these top-level sections (regex) in the scheduled default run; default = numbered domains
SECTION_RE = re.compile(os.environ.get("SECTION_REGEX", r"^\d\d[-.]"))
# Standing per-note sync: comma-separated vault-relative folders mirrored note-by-note
# into the tablet tree on every scheduled run (hash-gated). Default = the work section.
SYNC_FOLDERS = [f.strip().strip("/") for f in
                os.environ.get("SYNC_FOLDERS", "20.Work").split(",") if f.strip()]
# Mermaid -> SVG via the internal Kroki service (stacks/kroki). Horizontal diagrams are
# rewritten to top-down for the narrow tablet page unless MERMAID_FORCE_TD is off.
KROKI_URL = os.environ.get("KROKI_URL", "http://kroki:8000")
KROKI_TIMEOUT = int(os.environ.get("KROKI_TIMEOUT", "25"))
MERMAID_FORCE_TD = os.environ.get("MERMAID_FORCE_TD", "1").strip().lower() \
                   not in ("0", "false", "no", "off", "")
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
LOCKF = STATE/"queue.lock"          # held while a run processes the request queue
HEARTBEAT = STATE/"watcher.heartbeat"
LAYOUT_V = 2                        # bump to force a one-time tablet-layout migration
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
    except Exception: return {"sections":{}, "notes":{}}
def save_state(s): ST.write_text(json.dumps(s,indent=2))

# ---- markdown preprocessing (Obsidian -> plain markdown) ------------------
FM = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.S)       # tolerate CRLF frontmatter
DV = re.compile(r"```+dataview[a-z]*\r?\n.*?\r?\n```+", re.S)  # Dataview: dynamic, headless-blind
MERMAID = re.compile(r"```+mermaid[^\n]*\r?\n(.*?)\r?\n```+", re.S)
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

def _mermaid_td(src):
    """Rewrite horizontal flowcharts to top-down so a wide diagram fits the narrow
    tablet page. Only the orientation keyword is touched; the diagram is unchanged
    otherwise. No-op when MERMAID_FORCE_TD is off."""
    if not MERMAID_FORCE_TD:
        return src
    out=[]
    for ln in src.splitlines():
        ln=re.sub(r"^(\s*(?:flowchart|graph))\s+(?:LR|RL)\b", r"\1 TD", ln)
        ln=re.sub(r"^(\s*direction)\s+(?:LR|RL)\b", r"\1 TB", ln)
        out.append(ln)
    return "\n".join(out)

# WeasyPrint cannot draw HTML inside SVG <foreignObject>, which is how Mermaid renders
# labels by default, so every label came out blank (2026-10-30). This init directive
# makes Mermaid emit plain SVG <text> labels, and the neutral theme renders in clean
# greys on e-ink. Prepended to every diagram before it goes to Kroki.
MERMAID_INIT = os.environ.get("MERMAID_INIT",
    '%%{init: {"theme": "neutral", "htmlLabels": false, "flowchart": {"htmlLabels": false}}}%%')

def _mermaid_for_pdf(src):
    """Orientation rewrite (_mermaid_td) plus the PDF-safe init directive."""
    src2=_mermaid_td(src)
    if not MERMAID_INIT:
        return src2
    # Mermaid YAML front matter (--- ... ---) must stay on the first line: put the
    # directive right after it instead of in front of it.
    lines=src2.splitlines()
    if lines and lines[0].strip()=="---":
        for i in range(1,len(lines)):
            if lines[i].strip()=="---":
                return "\n".join(lines[:i+1]+[MERMAID_INIT]+lines[i+1:])
    return MERMAID_INIT+"\n"+src2

def _render_mermaid(src):
    """POST a Mermaid diagram to Kroki and return an ![](svg) image reference, or fall
    back to the diagram source (fenced, with a one-line note) if Kroki errors. Never
    raises -- a bad diagram must not fail the whole note."""
    src2=_mermaid_for_pdf(src)
    try:
        d=BUILD/"mermaid"; d.mkdir(parents=True, exist_ok=True)
        sha=hashlib.sha256(src2.encode("utf-8")).hexdigest()[:16]
        svg=d/f"{sha}.svg"
        if not (svg.exists() and svg.stat().st_size>0):
            req=urllib.request.Request(KROKI_URL.rstrip("/")+"/mermaid/svg",
                data=src2.encode("utf-8"),
                headers={"Content-Type":"text/plain"}, method="POST")
            with urllib.request.urlopen(req, timeout=KROKI_TIMEOUT) as r:
                data=r.read()
            if b"<svg" not in data[:4096]:
                raise RuntimeError("kroki returned no <svg>")
            svg.write_bytes(data)
        # percent-encode (keep "/") like the image embeds: a bare path with spaces is
        # truncated by pandoc's markdown reader; the %20 form survives and WeasyPrint
        # decodes it. max-width:100% in the device CSS caps the SVG to the page width.
        return f"![]({urllib.parse.quote(svg.as_posix(), safe='/')})"
    except Exception as e:
        log("mermaid render failed; falling back to source:", e)
        return ("*[Mermaid diagram — not rendered (Kroki unavailable); source below]*\n\n"
                "```\n"+src.strip()+"\n```")

def clean_md(text):
    global _IMG
    if _IMG is None: _IMG = _img_index()
    text = FM.sub("", text, count=1)                     # strip YAML frontmatter
    # Pull Mermaid blocks out FIRST (before %%-comment and Dataview stripping, since
    # Mermaid uses %% comments too) and park each behind a NUL sentinel, so later
    # regexes never touch a diagram's source or its emitted image reference.
    mermaid=[]
    def _mm(m):
        mermaid.append(_render_mermaid(m.group(1)))
        return f"\x00MERMAID{len(mermaid)-1}\x00"
    text = MERMAID.sub(_mm, text)
    text = re.sub(r"%%.*?%%", "", text, flags=re.S)      # strip Obsidian comments
    text = DV.sub("*[Dataview query — renders only in Obsidian]*", text)
    def _embed(m):
        tgt = m.group(1).split("|")[0].strip()           # drop display-size "|300"
        name = pathlib.Path(tgt).name
        if pathlib.Path(tgt).suffix.lower() in IMG_EXT:
            hit = _IMG.get(name)
            # Percent-encode the path (keep "/"). pandoc's markdown reader stops a bare URL
            # at the first space, so most vault paths ("1. Projects", "3. Resources", ...)
            # would silently lose the image; the %20-encoded form survives the reader and is
            # a valid URL WeasyPrint decodes back to the real file. WeasyPrint rasterises SVG
            # natively; PNG/JPG embeds use the same encoded path.
            return f"![]({urllib.parse.quote(hit.as_posix(), safe='/')})" if hit else ""
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
    for i, rep in enumerate(mermaid):                    # restore parked Mermaid blocks
        text = text.replace(f"\x00MERMAID{i}\x00", rep)
    return text

def note_markdown(md_path):
    rel = md_path.relative_to(VAULT).as_posix()[:-3]
    return f"\n\n# {rel}\n\n" + clean_md(md_path.read_text(encoding="utf-8",errors="replace"))

def tree_markdown(root):
    parts=[]
    for md in sorted(root.rglob("*.md")):
        parts.append(note_markdown(md))
    return "".join(parts)

def _device_css():
    # e-ink-friendly CSS injected into the HTML head (overrides pandoc's default template).
    # No @page headers/footers (WeasyPrint adds none by default).
    return (
        "<style>\n"
        f"@page {{ size: {PAGE_SIZE}; margin: 9mm 9mm 11mm 9mm; }}\n"
        "html { font-size: 12.5pt; }\n"
        "body { line-height: 1.4; max-width: none; margin: 0; padding: 0; }\n"
        "img, svg { max-width: 100%; height: auto; page-break-inside: avoid; }\n"
        "table { font-size: 0.9em; word-break: break-word; border-collapse: collapse; }\n"
        "pre, code { white-space: pre-wrap; word-break: break-word; }\n"
        "h1, h2, h3, h4, h5, h6 { page-break-after: avoid; }\n"
        "</style>\n"
    )

def render_pdf(name, md_text, out_pdf):
    # Render in a private scratch dir so concurrent renders (watcher + one-shot) never
    # collide on the same source/header file. pandoc writes its temp files in the CWD and
    # the image WORKDIR (/app) is root-owned, so run from this writable dir with TMPDIR=/tmp.
    work=pathlib.Path(tempfile.mkdtemp(dir=str(BUILD)))
    try:
        src=work/"in.md"; src.write_text(md_text, encoding="utf-8")
        hdr=work/"head.html"; hdr.write_text(_device_css(), encoding="utf-8")
        cmd=["pandoc", str(src), "-o", str(out_pdf), f"--pdf-engine={ENGINE}",
             "--metadata", f"title={name}", "--resource-path", str(VAULT),
             "--include-in-header", str(hdr)]   # device page size + e-ink CSS (PDF_PAGE_SIZE)
        env={**os.environ, "TMPDIR": "/tmp"}
        r=subprocess.run(cmd, capture_output=True, timeout=1200, cwd=str(work), env=env)
        if r.returncode!=0 or not out_pdf.exists() or out_pdf.stat().st_size==0:
            raise RuntimeError(f"pandoc failed: {r.stderr.decode(errors='replace')[-400:]}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

# ---- tablet (tailnet first, LAN fallback) ---------------------------------
_SSH=["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
RM_HOST=None   # resolved by pick_host()
def _ssh(cmd,timeout=60): return subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd],capture_output=True,timeout=timeout)
def _put(local, remote, timeout=300):
    """Copy a local file to the tablet with `cat`-over-SSH. The remote path is a single
    shell-quoted argument, so spaces/apostrophes/unicode are handled correctly on the
    tablet's shell regardless of its scp protocol (modern scp = SFTP, dropbear's = legacy
    SCP which shell-parses the path). ssh's exec channel is 8-bit clean for the PDF."""
    with open(local,"rb") as fh:
        r=subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",
                          "cat > "+shlex.quote(remote)], stdin=fh,
                         capture_output=True, timeout=timeout)
    if r.returncode!=0:
        raise RuntimeError(f"copy failed: {r.stderr.decode(errors='replace')[-200:]}")
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

# ---- delivery into the mirrored tree --------------------------------------
def _remote_dir(base_dest, rel_dir):
    return base_dest + (("/"+rel_dir) if rel_dir else "")
def _remote_path(base_dest, rel_dir, name, ext=".pdf"):
    """Remote PDF path keeping the REAL note/folder name (spaces and all)."""
    return f"{_remote_dir(base_dest, rel_dir)}/{name}{ext}"

def _deliver(rel_dir, name, md_text, base_dest):
    """Render `md_text` and copy it to base_dest/<rel_dir>/<name>.pdf, keeping the real
    name. Returns (remote_path, info) or (None, reason)."""
    if not md_text.strip(): return None, "empty (nothing to render)"
    work=pathlib.Path(tempfile.mkdtemp(dir=str(BUILD)))
    try:
        out=work/"out.pdf"
        render_pdf(name, md_text, out)
        mb=out.stat().st_size/1e6
        if mb>MAX_MB: return None, f"{mb:.1f}MB > {MAX_MB}MB guard, skipped"
        dest_dir=_remote_dir(base_dest, rel_dir)
        _ssh("mkdir -p "+shlex.quote(dest_dir))
        remote=_remote_path(base_dest, rel_dir, name)
        _put(out, remote)
        return remote, f"{mb:.1f}MB"
    finally:
        shutil.rmtree(work, ignore_errors=True)

# ---- request queue (the Vault channel) ------------------------------------
def _resolve_in_vault(relpath):
    """Resolve a vault-relative path, refusing anything that escapes the vault."""
    p=(VAULT/relpath).resolve()
    if not str(p).startswith(str(VAULT.resolve())+os.sep) and p!=VAULT.resolve():
        raise ValueError(f"path escapes vault: {relpath}")
    return p

def _reldir_of(path):
    """Vault-relative directory of a path (posix), '' for the vault root."""
    rd=path.parent.relative_to(VAULT).as_posix()
    return "" if rd=="." else rd

def _jobs_for(req):
    """Return [(rel_dir, name, markdown)] for a request, honouring scope + mode.
    rel_dir mirrors the vault tree; name is the real note/folder name (kept with spaces)."""
    scope=req.get("scope","note"); mode=req.get("mode","combined")
    if scope=="vault":
        secs=[d for d in sorted(VAULT.iterdir()) if d.is_dir() and SECTION_RE.match(d.name)]
        if mode=="combined":
            return [("", "Vault", "".join(tree_markdown(s) for s in secs))]
        # per-note == per-section: one combined PDF inside each section folder
        return [(s.name, s.name, tree_markdown(s)) for s in secs]
    path=(req.get("path") or "").strip().lstrip("/")
    if not path: raise ValueError("scope needs a path")
    target=_resolve_in_vault(path)
    if scope=="note":
        if target.suffix.lower()!=".md" or not target.is_file(): raise ValueError(f"not a note: {path}")
        return [(_reldir_of(target), target.stem, note_markdown(target))]
    if scope=="folder":
        if not target.is_dir(): raise ValueError(f"not a folder: {path}")
        if mode=="per-note":
            return [(_reldir_of(md), md.stem, note_markdown(md)) for md in sorted(target.rglob("*.md"))]
        # combined: one PDF inside the folder it covers
        return [(target.relative_to(VAULT).as_posix(), target.name or "Vault", tree_markdown(target))]
    raise ValueError(f"unknown scope: {scope}")

def _acquire_lock():
    f=open(LOCKF,"w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return f
    except OSError:
        f.close(); return None
def _release_lock(f):
    try: fcntl.flock(f, fcntl.LOCK_UN)
    finally: f.close()

def process_queue():
    """Process every queued request under a /state lock (so the watcher and the scheduled
    one-shot never process the same request twice). Returns the number handled, 0 if the
    queue is empty, -1 if the tablet is asleep (requests left queued), or -2 if the lock is
    held by the other runner (try again later)."""
    if not (QDIR.exists() and sorted(QDIR.glob("*.json"))): return 0
    lock=_acquire_lock()
    if lock is None:
        log("request queue lock held by the other runner; skipping this pass"); return -2
    try:
        reqs=sorted(QDIR.glob("*.json"))
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
                sub=(req.get("target_subdir") or "").strip().strip("/")
                base=RM_DEST+("/"+"/".join(sub.split("/")) if sub else "")
                delivered=[]; errs=[]
                try:
                    jobs=_jobs_for(req)
                    for rd,name,md in jobs:
                        # a custom target_subdir flattens into that subdir (keeping the real
                        # name); otherwise mirror the vault tree under RM_DEST.
                        use_rd="" if sub else rd
                        try:
                            remote,info=_deliver(use_rd,name,md,base)
                            if remote: delivered.append(remote); log("delivered",remote,f"({info})")
                            else: errs.append(f"{name}: {info}")
                        except Exception as e: errs.append(f"{name}: {e}")
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
    finally:
        _release_lock(lock)

# ---- scheduled whole-tree reconcile (the one-shot default) ----------------
def _under_sync(rel):
    """True if a vault-relative note path is under one of the SYNC_FOLDERS."""
    return any(rel.startswith(sf+"/") for sf in SYNC_FOLDERS)

def _migrate_tablet(state):
    """The one-time tablet migrations themselves (idempotent): (1) rename the vault folder
    Homelab -> kodiak-codex, keeping .sdr sidecars, never deleting; (2) move from the old
    flat layout to the mirrored tree by removing the flat root PDFs (leaving .sdr) and forcing
    a rebuild. Requires RM_HOST already set (tablet awake). Sets the state `layout` marker."""
    if RM_DEST_OLD and RM_DEST_OLD!=RM_DEST:
        chk=_ssh("[ -d {} ] && [ ! -e {} ] && echo yes || echo no".format(
            shlex.quote(RM_DEST_OLD), shlex.quote(RM_DEST)))
        if chk.stdout.strip()==b"yes":
            _ssh("mkdir -p {} && mv {} {}".format(
                shlex.quote(os.path.dirname(RM_DEST)),
                shlex.quote(RM_DEST_OLD), shlex.quote(RM_DEST)))
            log(f"migrated tablet folder {RM_DEST_OLD} -> {RM_DEST} (kept .sdr)")
    # remove old flat PDFs sitting directly under RM_DEST (old per-section dumps and one-off
    # sends like Bench-Layout.pdf); subdirectories and .sdr sidecars are left.
    _ssh("rm -f "+shlex.quote(RM_DEST)+"/*.pdf")
    log("layout migration: removed flat root PDFs (kept .sdr/subdirs); rebuilding into the tree")
    state["sections"]={}; state["notes"]={}; state["layout"]=LAYOUT_V

def maybe_migrate():
    """Run the one-time tablet migrations exactly once, BEFORE either runner delivers anything
    (so the folder rename always precedes a write to the new folder and old .sdr sidecars are
    preserved). Guarded by the state `layout` marker and serialised by the SAME queue lock the
    deliveries take, so the watcher and the one-shot cooperate. A no-op once migrated; deferred
    (retried later) if the tablet is asleep or the other runner holds the lock."""
    if load_state().get("layout")==LAYOUT_V:
        return
    lock=_acquire_lock()
    if lock is None:
        return                              # the other runner holds the lock; retry next time
    try:
        state=load_state()
        if state.get("layout")==LAYOUT_V:
            return
        if not pick_host():
            log("migration deferred: tablet asleep"); return
        _migrate_tablet(state)
        save_state(state)
    finally:
        _release_lock(lock)

def reconcile_sync_folders(state, failed):
    """Mirror every note under SYNC_FOLDERS per-note into RM_DEST/<vault path>/<name>.pdf,
    re-rendering only changed notes and removing PDFs whose source note disappeared."""
    notes=state.setdefault("notes",{})
    seen=set(); built=skipped=removed=0
    for sf in SYNC_FOLDERS:
        root=VAULT/sf
        if not root.is_dir():
            log(f"SYNC_FOLDERS: '{sf}' is not a vault folder, skipping"); continue
        for md in sorted(root.rglob("*.md")):
            rel=md.relative_to(VAULT).as_posix()
            seen.add(rel)
            body=note_markdown(md)
            h=hashlib.md5(body.encode()).hexdigest()
            prev=notes.get(rel,{})
            if prev.get("hash")==h and prev.get("remote"):
                skipped+=1; continue
            try:
                remote,info=_deliver(_reldir_of(md), md.stem, body, RM_DEST)
            except Exception as e:
                failed.append(f"{rel}: {e}"); log("render FAILED",rel,e); continue
            if remote:
                notes[rel]={"hash":h,"remote":remote}; built+=1; log("delivered",remote,f"({info})")
            else:
                failed.append(f"{rel}: {info}"); log("skip",rel,info)
    for rel in [r for r in list(notes) if r not in seen and _under_sync(r)]:
        rem=notes[rel].get("remote")
        if rem: _ssh("rm -f "+shlex.quote(rem)); log("removed (source gone)",rem)  # leaves the .sdr
        del notes[rel]; removed+=1
    log(f"sync folders {SYNC_FOLDERS}: +{built} ~{skipped}unchanged -{removed}")

def reconcile_sections(state, failed):
    """One combined PDF per top-level section (RM_DEST/<section>/<section>.pdf), skipping any
    section that SYNC_FOLDERS already mirrors per-note (to avoid a duplicate dump)."""
    tracked=state.setdefault("sections",{})
    covered={sf for sf in SYNC_FOLDERS if "/" not in sf}   # whole top-level sections
    sections=[d for d in sorted(VAULT.iterdir()) if d.is_dir() and SECTION_RE.match(d.name)]
    log(f"{len(sections)} section(s): {[s.name for s in sections]}; per-note: {sorted(covered)}")
    built=skipped=removed=0; seen=set()
    for sec in sections:
        if sec.name in covered:
            if sec.name in tracked:                       # was combined, now per-note: clean up
                _ssh("rm -f "+shlex.quote(f"{RM_DEST}/{sec.name}/{sec.name}.pdf")); del tracked[sec.name]
            continue
        md=tree_markdown(sec)
        if not md.strip(): continue
        seen.add(sec.name)
        h=hashlib.md5(md.encode()).hexdigest()
        if tracked.get(sec.name,{}).get("hash")==h:
            skipped+=1; continue
        try:
            remote,info=_deliver(sec.name, sec.name, md, RM_DEST)
        except Exception as e:
            failed.append(f"{sec.name}: {e}"); log("render FAILED",sec.name,e); continue
        if remote:
            tracked[sec.name]={"hash":h}; built+=1; log("delivered",remote,f"({info})")
        else:
            failed.append(f"{sec.name}: {info}"); log("too big/empty",sec.name,info)
    for name in [n for n in list(tracked) if n not in seen and n not in covered]:
        _ssh("rm -f "+shlex.quote(f"{RM_DEST}/{name}/{name}.pdf"))   # leaves the .sdr
        log("removed",f"{name}/{name}.pdf"); del tracked[name]; removed+=1
    log(f"section sync: +{built} ~{skipped}unchanged -{removed}")

def run_reconcile():
    maybe_migrate()                      # one-time (rename + flat->tree), before any delivery
    if not pick_host(): log("tablet asleep; skipping scheduled reconcile"); return
    state=load_state()
    _ssh("mkdir -p "+shlex.quote(RM_DEST))
    failed=[]
    reconcile_sync_folders(state, failed)
    reconcile_sections(state, failed)
    save_state(state)
    log(f"reconcile OK: !{len(failed)} failed")
    if failed: notify(f"obsidian-to-koreader: {len(failed)} failed","\n".join(failed[:8]))

# ---- watcher (near-instant sends) -----------------------------------------
def run_watcher():
    poll=max(5,int(os.environ.get("POLL_SECONDS","20")))
    bmax=max(poll,int(os.environ.get("SLEEP_BACKOFF_MAX","300")))
    backoff=poll; asleep_until=0.0
    log(f"watcher start (poll={poll}s, asleep-backoff cap={bmax}s, queue={QDIR})")
    while True:
        try: HEARTBEAT.write_text(now_iso())
        except Exception: pass
        try:
            reqs=sorted(QDIR.glob("*.json")) if QDIR.exists() else []
            if reqs and time.time()>=asleep_until:
                maybe_migrate()                       # one-time, before delivering anything
                n=process_queue()
                if n==-1:                                 # tablet asleep -> back off
                    backoff=min(bmax, max(poll, backoff*2))
                    asleep_until=time.time()+backoff
                    log(f"tablet asleep; next probe in ~{backoff}s")
                elif n==-2:                               # lock held by the one-shot; retry soon
                    pass
                else:                                     # awake (handled >=0) -> reset backoff
                    backoff=poll; asleep_until=0.0
        except Exception as e:
            log("watcher loop error:", e)
            notify("obsidian-to-koreader watcher error", f"{type(e).__name__}: {e}")
        try: HEARTBEAT.write_text(now_iso())
        except Exception: pass
        time.sleep(poll)

# ---- main -----------------------------------------------------------------
def main():
    if not VAULT.exists(): log(f"{VAULT} missing (obsidian-headless not mounted?)"); return
    mode=os.environ.get("MODE","oneshot").strip().lower()
    if mode=="watch":
        run_watcher(); return
    # scheduled one-shot: migrate once (before any delivery), drain the queue as a fallback
    # (in case the watcher is down), then reconcile the tree (sync folders + sections).
    maybe_migrate()
    handled=process_queue()
    if handled>0: log(f"vault channel (one-shot fallback): {handled} request(s) handled")
    run_reconcile()

if __name__=="__main__":
    try: main()
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"; log("FATAL:",msg); notify("obsidian-to-koreader failed",msg); raise
