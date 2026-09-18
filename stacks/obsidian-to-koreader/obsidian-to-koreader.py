#!/usr/bin/env python3
"""obsidian-to-koreader: render each vault section to a PDF and put it on the tablet.

Reads the headless-synced Obsidian vault (VAULT_DIR), and for each top-level section
(dirs like 00-Inbox, 10-Homelab, ...) concatenates its Markdown into one PDF via pandoc
(weasyprint engine), then copies the PDFs into RM_DEST (/home/root/books/Homelab) over
dropbear SSH so they're readable in KOReader. Reconciles: rebuilds changed sections,
removes PDFs for sections that no longer exist. A per-section size guard skips anything
that would render too large for the tablet.

Read-only on the vault (never writes back). Opportunistic (tablet-awake); Pushover on
failure. Run periodically by a Komodo procedure.
"""
from __future__ import annotations
import os, json, subprocess, urllib.request, urllib.parse, pathlib, datetime, shlex, re, hashlib

VAULT   = pathlib.Path(os.environ.get("VAULT_DIR", "/vault"))
STATE   = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
RM_DEST = os.environ.get("RM_DEST", "/home/root/books/Homelab")
RM_HOST = os.environ.get("RM_HOST", "10.1.30.245")
RM_USER = os.environ.get("RM_USER", "root")
RM_PW   = os.environ.get("RM_PW", "")
MAX_MB  = float(os.environ.get("MAX_PDF_MB", "40"))
# only these top-level sections (regex); default = numbered domains
SECTION_RE = re.compile(os.environ.get("SECTION_REGEX", r"^\d\d-"))
STATE.mkdir(parents=True, exist_ok=True)
BUILD = STATE/"build"; BUILD.mkdir(exist_ok=True)
ST = STATE/"obsidian-to-koreader.json"

def log(*a): print(datetime.datetime.now().strftime("%F %T"), *a, flush=True)
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
FM = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.S)   # tolerate CRLF frontmatter
def clean_md(text):
    text = FM.sub("", text, count=1)                    # strip YAML frontmatter
    text = re.sub(r"!\[\[[^\]]+\]\]", "", text)         # drop embeds
    text = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", text)  # [[a|b]] -> b
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)     # [[a]] -> a
    return text

def section_markdown(secdir):
    parts=[]
    for md in sorted(secdir.rglob("*.md")):
        rel=md.relative_to(secdir)
        parts.append(f"\n\n# {rel.as_posix()[:-3]}\n\n" + clean_md(md.read_text(encoding="utf-8",errors="replace")))
    return "".join(parts)

def render_pdf(name, md_text, out_pdf):
    src=BUILD/f"{name}.md"; src.write_text(md_text, encoding="utf-8")
    r=subprocess.run(["pandoc", str(src), "-o", str(out_pdf), "--pdf-engine=weasyprint",
                      "--metadata", f"title={name}", "-V", "papersize=a5"],
                     capture_output=True, timeout=900)
    if r.returncode!=0 or not out_pdf.exists() or out_pdf.stat().st_size==0:
        raise RuntimeError(f"pandoc failed: {r.stderr.decode(errors='replace')[-400:]}")

# ---- tablet ---------------------------------------------------------------
_SSH=["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
def _ssh(cmd,timeout=60): return subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd],capture_output=True,timeout=timeout)
# modern scp = SFTP: pass the remote path raw (no shell-quote); spaces are fine.
def _scp(local,remote,timeout=180): subprocess.run(["sshpass","-p",RM_PW,"scp",*_SSH,str(local),f"{RM_USER}@{RM_HOST}:{remote}"],check=True,timeout=timeout)
def tablet_up():
    try: return _ssh("echo ok",timeout=15).stdout.strip()==b"ok"
    except Exception: return False

# ---- main -----------------------------------------------------------------
def main():
    if not VAULT.exists(): log(f"{VAULT} missing (obsidian-headless not mounted?)"); return
    if not tablet_up(): log("tablet asleep; skipping"); return
    _ssh(f"mkdir -p {shlex.quote(RM_DEST)}")
    state=load_state(); tracked=state.setdefault("sections",{})
    sections=[d for d in sorted(VAULT.iterdir()) if d.is_dir() and SECTION_RE.match(d.name)]
    log(f"{len(sections)} section(s): {[s.name for s in sections]}")
    built=skipped=removed=0; failed=[]; seen=set()
    for sec in sections:
        md=section_markdown(sec)
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
    log(f"obsidian-to-koreader OK: +{built} ~{skipped}unchanged -{removed}, !{len(failed)} failed")
    if failed: notify(f"obsidian-to-koreader: {len(failed)} failed","\n".join(failed[:8]))

if __name__=="__main__":
    try: main()
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"; log("FATAL:",msg); notify("obsidian-to-koreader failed",msg); raise
