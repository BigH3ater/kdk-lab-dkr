#!/usr/bin/env python3
"""remarkable-ocr: reMarkable handwriting -> OCR (Ollama qwen2.5vl) -> Obsidian
vault, with `@dia` pages converted to a topology SVG (kodiak_diagram).

Opportunistic + durable. `pipeline.py run` does:
  enqueue  -- if the tablet is reachable: pull changed notebook pages (by md5),
              rmc-render at 600px on an auto-chosen background, drop into the
              queue with a sidecar (notebook -> vault section, page, hash).
  drain    -- if Ollama is reachable: OCR each queued page; `@dia` -> topology
              extraction -> SVG; write one markdown note per page into the
              matching vault section; move pending -> done. Failures -> failed
              with a retry counter. Neither half loses work if its dependency is
              offline (the tablet sleeps; the 4090 workstation is intermittent).
"""
from __future__ import annotations
import os, sys, json, re, time, base64, subprocess, urllib.request, pathlib, datetime, tempfile
sys.path.insert(0, "/app/diagram")            # kodiak_diagram + topology_svg (mounted)

RM_HOST = os.environ.get("RM_HOST", "10.1.30.245")
RM_USER = os.environ.get("RM_USER", "root")
RM_PW   = os.environ.get("RM_PW", "")
OLLAMA  = os.environ.get("OLLAMA_URL", "http://ollama.kmkdp.com:11434").rstrip("/")
MODEL   = os.environ.get("OCR_MODEL", "qwen2.5vl:7b")
QUEUE   = pathlib.Path(os.environ.get("QUEUE_DIR", "/queue"))
VAULT   = pathlib.Path(os.environ.get("VAULT_DIR", "/vault"))
FOLDER  = os.environ.get("RM_FOLDER", "Kodiak Notebooks")
MAX_RETRY = int(os.environ.get("MAX_RETRY", "4"))
MIN_STROKES = int(os.environ.get("MIN_STROKES", "3"))   # skip ~blank pages (vision models hallucinate on empty images)
XO = "/home/root/.local/share/remarkable/xochitl"
SECTION = {"Homelab":"10-Homelab","Personal":"20-Personal","Books":"30-Books",
           "Development":"40-Development","Learning":"50-Learning","Work":"60-Work",
           "Inbox":"00-Inbox/Unsorted"}
DEFAULT_SECTION = "00-Inbox/Unsorted"
PEND, DONE, FAIL = QUEUE/"pending", QUEUE/"done", QUEUE/"failed"
STATE = QUEUE/"state.json"

def log(*a): print(datetime.datetime.now().strftime("%F %T"), *a, flush=True)
def load_state(): 
    try: return json.loads(STATE.read_text())
    except Exception: return {}
def save_state(s): STATE.write_text(json.dumps(s, indent=0))
def notify(title, msg):
    tok, usr = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if not (tok and usr): return
    import urllib.parse
    body = urllib.parse.urlencode({"token":tok,"user":usr,"title":title,"message":msg,"priority":1}).encode()
    try: urllib.request.urlopen("https://api.pushover.net/1/messages.json",data=body,timeout=10)
    except Exception: pass

# ---- tablet (LAN dropbear, password auth) ---------------------------------
_SSH = ["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
def _sshpass(*args, **kw): return subprocess.run(["sshpass","-p",RM_PW,*args], **kw)
def tablet_up():
    try: return _sshpass("ssh",*_SSH,f"{RM_USER}@{RM_HOST}","echo ok",capture_output=True,timeout=15).stdout.strip()==b"ok"
    except Exception: return False
def tablet_sh(cmd): return _sshpass("ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd,capture_output=True,timeout=90).stdout.decode(errors="replace")
def tablet_scp(remote,local): _sshpass("scp",*_SSH,f"{RM_USER}@{RM_HOST}:{remote}",str(local),check=True,timeout=90)

MANIFEST_SH = r'''XO=%s
FID=$(for m in $XO/*.metadata; do grep -q '"visibleName": *"%s"' "$m" 2>/dev/null && grep -q CollectionType "$m" && basename "$m" .metadata; done | head -n 1)
[ -z "$FID" ] && exit 0
for m in $XO/*.metadata; do
  grep -q "\"parent\": *\"$FID\"" "$m" 2>/dev/null || continue
  u=$(basename "$m" .metadata)
  nb=$(grep -oE '"visibleName" *: *"[^"]*"' "$m" | head -n1 | sed 's/.*: *"//; s/"$//')
  for p in "$XO/$u"/*.rm; do [ -f "$p" ] || continue
    echo "$nb|$u|$(basename "$p" .rm)|$(md5sum "$p" | cut -d' ' -f1)"; done
done''' % (XO, FOLDER)

# ---- rendering (rmc -> auto-bg PNG @ ~600px) -------------------------------
def rmc_svg(rm_path, svg_path):
    subprocess.run(["rmc","-t","svg",str(rm_path),"-o",str(svg_path)],capture_output=True,timeout=120)
def stroke_count(svg_text):
    return len(re.findall(r"<(?:path|polyline|line)\b", svg_text))
def svg_to_png(svg_path, png_path, width=600):
    import cairosvg, re as _re
    svg = pathlib.Path(svg_path).read_text()
    cols = _re.findall(r'stroke:rgb\((\d+), (\d+), (\d+)\)', svg)
    light = sum(1 for r,g,b in cols if int(r)+int(g)+int(b) > 600)
    bg = "#000000" if cols and light > len(cols)/2 else "#FFFFFF"
    cairosvg.svg2png(url=str(svg_path), write_to=str(png_path), background_color=bg, output_width=width)

# ---- Ollama ---------------------------------------------------------------
def ollama_up():
    try: urllib.request.urlopen(OLLAMA+"/api/tags",timeout=6); return True
    except Exception: return False
def ollama_gen(prompt, png_path, npredict=1500):
    b64 = base64.b64encode(pathlib.Path(png_path).read_bytes()).decode()
    body = json.dumps({"model":MODEL,"prompt":prompt,"images":[b64],"stream":True,
                       "options":{"temperature":0,"num_predict":npredict}}).encode()
    req = urllib.request.Request(OLLAMA+"/api/generate",data=body,headers={"Content-Type":"application/json"})
    out=[]
    for line in urllib.request.urlopen(req,timeout=300):
        o=json.loads(line)
        if o.get("response"): out.append(o["response"])
        if o.get("done"): break
    return "".join(out)

OCR_PROMPT=("Transcribe this handwritten note to clean Markdown. Preserve headings, bullets and "
            "checkboxes. If a region is a drawn diagram, leave it and keep any @dia tag verbatim. "
            "Output only the transcription.")
TOPO_PROMPT=('Extract the network topology as STRICT JSON only: '
             '{"title":"...","nodes":[{"id","label"}],"edges":[{"from","to"}]}. '
             'Include an edge ONLY for a line you can actually trace between two nodes; do not '
             'invent redundant/mesh links. id may equal label.')

def make_diagram(png_path, out_svg):
    raw = ollama_gen(TOPO_PROMPT, png_path, npredict=1200)
    m = re.search(r"\{.*\}", raw, re.S)
    spec = json.loads(m.group(0))
    import topology_svg
    out_svg.write_text(topology_svg.render(spec))
    return spec

# ---- enqueue --------------------------------------------------------------
def enqueue():
    if not tablet_up(): log("enqueue: tablet unreachable, skipping"); return 0
    state = load_state(); man = tablet_sh(MANIFEST_SH); n=0
    for line in man.splitlines():
        parts=line.split("|")
        if len(parts)!=4: continue
        nb,doc,page,md5 = parts
        if state.get(md5,{}).get("done"): continue
        if (PEND/f"{md5}.json").exists(): continue
        with tempfile.TemporaryDirectory() as td:
            rm=pathlib.Path(td)/"p.rm"; svg=pathlib.Path(td)/"p.svg"
            try: tablet_scp(f"{XO}/{doc}/{page}.rm", rm)
            except Exception as e: log("scp fail",page,e); continue
            rmc_svg(rm,svg)
            if not svg.exists() or svg.stat().st_size==0: log("render empty",page[:8]); continue
            sc = stroke_count(svg.read_text())
            if sc < MIN_STROKES:
                # ~blank page: don't OCR it (vision model would hallucinate). Mark
                # done so we don't re-render it every run; it re-enqueues if written on
                # (the md5 changes).
                state.setdefault(md5,{})["done"]=time.time(); log("skip blank",page[:8],f"strokes={sc}"); continue
            svg_to_png(svg, PEND/f"{md5}.png")
        (PEND/f"{md5}.json").write_text(json.dumps(
            {"notebook":nb,"section":SECTION.get(nb,DEFAULT_SECTION),"page":page,"doc":doc,
             "md5":md5,"ts":time.time()}))
        state.setdefault(md5,{})["queued"]=time.time(); n+=1
        log("enqueued",nb,page[:8],md5[:8])
    save_state(state); log(f"enqueue: {n} new page(s)"); return n

# ---- drain ----------------------------------------------------------------
def slug(s): return re.sub(r"[^A-Za-z0-9 _-]","",s).strip()[:60] or "note"
def clean_ocr(t):
    # qwen often wraps the whole transcription in a ```markdown ... ``` fence; unwrap it.
    t=t.strip()
    m=re.match(r"^```[a-zA-Z]*\n(.*)\n```$", t, re.S)
    return (m.group(1).strip() if m else t)
def drain():
    if not ollama_up(): log("drain: Ollama unreachable, queue held"); return 0, []
    state=load_state(); n=0; failed=[]
    for sidecar in sorted(PEND.glob("*.json")):
        meta=json.loads(sidecar.read_text()); md5=meta["md5"]; png=PEND/f"{md5}.png"
        if not png.exists(): sidecar.unlink(); continue
        try:
            text = clean_ocr(ollama_gen(OCR_PROMPT, png))
            has_dia = bool(re.search(r"@?\bdia\b", text, re.I))
            secdir = VAULT/meta["section"]; secdir.mkdir(parents=True,exist_ok=True)
            date=datetime.date.today().isoformat()
            base=f"{date} {meta['notebook']} {meta['page'][:6]}"
            body=[f"---","tags: [remarkable, ocr]",f"source: {meta['notebook']}",
                  f"page: {meta['page']}",f"captured: {date}","---","",text.strip(),""]
            if has_dia:
                try:
                    svgp=secdir/f"{slug(base)}.svg"; spec=make_diagram(png,svgp)
                    # Embed the auto-extracted topology so it can be corrected. The
                    # vision model over/under-connects crossed lines; edit this block
                    # and re-run topology_svg.py to regenerate the SVG (review gate).
                    body += ["", f"![[{svgp.name}]]", "",
                             "> [!note]- Auto-extracted topology (edit + re-render if wrong)",
                             "> ```json", *[f"> {l}" for l in json.dumps(spec,indent=2).splitlines()],
                             "> ```", ""]
                    log("  @dia -> diagram",svgp.name)
                except Exception as e:
                    body += ["", f"> [!warning] diagram extraction failed: {e}",""]
            (secdir/f"{slug(base)}.md").write_text("\n".join(body))
            (DONE/png.name).write_bytes(png.read_bytes()); png.unlink(); sidecar.unlink()
            state.setdefault(md5,{})["done"]=time.time(); n+=1
            log("ocr ->", meta["section"], slug(base))
        except Exception as e:
            meta["retries"]=meta.get("retries",0)+1; sidecar.write_text(json.dumps(meta))
            log("drain error",md5[:8],meta["retries"],e)
            if meta["retries"]>=MAX_RETRY:
                (FAIL/png.name).write_bytes(png.read_bytes()); (FAIL/sidecar.name).write_text(sidecar.read_text())
                png.unlink(); sidecar.unlink()
                failed.append(f"{meta['notebook']}/{meta['page'][:6]}: {type(e).__name__}: {e}")
                log("  -> failed (max retry)")
    save_state(state); log(f"drain: {n} page(s) processed"); return n, failed

def main(mode):
    if mode in ("run","enqueue"): enqueue()
    failed=[]
    if mode in ("run","drain"): _, failed = drain()
    if failed:
        notify(f"remarkable-ocr: {len(failed)} page(s) failed",
               "OCR gave up after retries (moved to failed/):\n" + "\n".join(failed[:8]))

if __name__=="__main__":
    mode = sys.argv[1] if len(sys.argv)>1 else "run"
    try:
        main(mode)
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"
        log("FATAL:",msg); notify("remarkable-ocr crashed", msg); raise
