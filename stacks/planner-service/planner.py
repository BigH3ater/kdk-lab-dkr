#!/usr/bin/env python3
"""planner-service: pull the Proton calendar (ICS share link) and produce a daily
+ week-ahead planner -- a Kodiak-brand PDF pushed to the reMarkable, and a Markdown
agenda written into the Obsidian vault (90-Meta/Planners/, which syncs to clients).

Proton Calendar's ICS plugin in the client renders events live and stores nothing
on disk, so the lab cannot read them from the vault; instead this fetches the same
"Share via link" ICS URL directly (ICS_URL, op-injected). One-shot, run daily.
Tablet push is opportunistic (only when the Paper Pro is awake on Wi-Fi); the vault
note is always written. Pages Pushover on failure.
"""
from __future__ import annotations
import os, sys, json, subprocess, urllib.request, pathlib, datetime, time
sys.path.insert(0, "/app/tpl")            # kodiak_lib (mounted from remarkable/pdf-templates)

ICS_URL = os.environ.get("ICS_URL", "")
VAULT   = pathlib.Path(os.environ.get("VAULT_DIR", "/vault"))
STATE   = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
DAYS    = int(os.environ.get("WEEK_DAYS", "7"))
TZ      = os.environ.get("TZ", "America/Chicago")
RM_HOST = os.environ.get("RM_HOST", "10.1.30.245")
RM_USER = os.environ.get("RM_USER", "root")
RM_PW   = os.environ.get("RM_PW", "")
RM_FOLDER_UUID = os.environ.get("RM_FOLDER_UUID", "a6af6fd7-bfa4-4895-bedc-6c286d61d819")  # "Kodiak Codex"
PLANNERS = VAULT/"90-Meta"/"Planners"
STATE.mkdir(parents=True, exist_ok=True)
PLANNERS.mkdir(parents=True, exist_ok=True)

def log(*a): print(datetime.datetime.now().strftime("%F %T"), *a, flush=True)
def notify(title, msg):
    tok, usr = os.environ.get("PUSHOVER_TOKEN"), os.environ.get("PUSHOVER_USER")
    if not (tok and usr): return
    import urllib.parse
    body=urllib.parse.urlencode({"token":tok,"user":usr,"title":title,"message":msg,"priority":1}).encode()
    try: urllib.request.urlopen("https://api.pushover.net/1/messages.json",data=body,timeout=10)
    except Exception: pass

# ---- calendar -------------------------------------------------------------
def fetch_events(start_date, end_date):
    """Return [(date, start_dt_or_None, end_dt_or_None, summary, all_day)] in [start,end)."""
    import icalendar, recurring_ical_events
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(TZ)
    raw = urllib.request.urlopen(ICS_URL, timeout=30).read()
    cal = icalendar.Calendar.from_ical(raw)
    occ = recurring_ical_events.of(cal).between(start_date, end_date)
    out=[]
    for ev in occ:
        summ = str(ev.get("SUMMARY","(untitled)"))
        s = ev.get("DTSTART").dt if ev.get("DTSTART") else None
        e = ev.get("DTEND").dt   if ev.get("DTEND")   else None
        all_day = not isinstance(s, datetime.datetime)
        if isinstance(s, datetime.datetime):
            s = s.astimezone(tz); 
            if isinstance(e, datetime.datetime): e = e.astimezone(tz)
            d = s.date()
        else:
            d = s  # date
        out.append((d, s, e, summ, all_day))
    out.sort(key=lambda t: (t[0], (t[1].hour, t[1].minute) if isinstance(t[1], datetime.datetime) else (-1,-1)))
    return out

def hhmm(dt): return dt.strftime("%-I:%M%p").lower().replace(":00","")

# ---- render (Kodiak brand SVG -> PDF) -------------------------------------
def render_pdf(today, events, out_base):
    import kodiak_lib as K
    days = [today + datetime.timedelta(days=i) for i in range(DAYS)]
    by_day = {}
    for d,s,e,summ,ad in events: by_day.setdefault(d, []).append((s,e,summ,ad))

    svg = K.SVG()
    svg.header(today.strftime("%A"), right=today.strftime("%b %-d, %Y").upper())
    y = 250
    svg.label(K.ML, y, "TODAY"); y += 40
    todays = by_day.get(today, [])
    if not todays:
        svg.text(K.ML, y+30, "No scheduled events", 30, fill=K.MUTED); y += 70
    else:
        for s,e,summ,ad in todays:
            t = "all day" if ad else (hhmm(s) + (f"-{hhmm(e)}" if isinstance(e, datetime.datetime) else ""))
            svg.checkbox(K.ML, y, 34)
            svg.text(K.ML+70, y+27, t, 26, fill=K.BLUELBL, weight="bold")
            svg.text(K.ML+300, y+27, summ[:46], 30, fill=K.TEXT)
            y += 58
    # notes / journal area filling the gap under today's agenda
    ny = y + 30
    if ny < 860:
        svg.label(K.ML, ny, "NOTES"); ny += 46
        while ny < 880:
            svg.line(K.ML, ny, K.MR, ny, stroke=K.DIV, sw=1); ny += 56
    # week-ahead strip
    y = 900; svg.line(K.ML, y, K.MR, y, stroke=K.DIV, sw=2); y += 50
    svg.label(K.ML, y, "WEEK AHEAD"); y += 50
    for d in days[1:]:
        evs = by_day.get(d, [])
        svg.text(K.ML, y+22, d.strftime("%a %-d").upper(), 24, fill=K.GRAY, weight="bold")
        if not evs:
            svg.text(K.ML+220, y+22, "—", 24, fill=K.DIV)
        else:
            line = "   ".join(("" if ad else hhmm(s)+" ")+summ[:22] for s,e,summ,ad in evs[:3])
            svg.text(K.ML+220, y+22, line[:60], 24, fill=K.TEXT)
        svg.line(K.ML, y+44, K.MR, y+44, stroke=K.DIV, sw=1); y += 78
    return svg.save(out_base)   # writes .svg/.pdf/.png, returns base (may add -dark suffix)

# ---- vault note -----------------------------------------------------------
def write_note(today, events):
    by_day={}
    for d,s,e,summ,ad in events: by_day.setdefault(d,[]).append((s,e,summ,ad))
    lines=["---","tags: [planner]",f"date: {today.isoformat()}","source: Proton Calendar","---","",
           f"# {today.strftime('%A, %B %-d, %Y')}","","## Today",""]
    for s,e,summ,ad in by_day.get(today,[]) or []:
        t="all day" if ad else hhmm(s)
        lines.append(f"- [ ] **{t}** {summ}")
    if not by_day.get(today): lines.append("- No scheduled events")
    lines += ["","## Week ahead",""]
    for i in range(1,DAYS):
        d=today+datetime.timedelta(days=i); evs=by_day.get(d,[])
        lines.append(f"### {d.strftime('%A %b %-d')}")
        for s,e,summ,ad in evs: lines.append(f"- {'all day' if ad else hhmm(s)} — {summ}")
        if not evs: lines.append("- —")
        lines.append("")
    p=PLANNERS/f"{today.isoformat()}.md"; p.write_text("\n".join(lines)); return p

# ---- tablet push (stable doc, replaced daily) -----------------------------
_SSH=["-o","StrictHostKeyChecking=accept-new","-o","UserKnownHostsFile=/dev/null","-o","ConnectTimeout=8"]
def _ssh(cmd): return subprocess.run(["sshpass","-p",RM_PW,"ssh",*_SSH,f"{RM_USER}@{RM_HOST}",cmd],capture_output=True,timeout=60)
def _scp(local,remote): subprocess.run(["sshpass","-p",RM_PW,"scp",*_SSH,str(local),f"{RM_USER}@{RM_HOST}:{remote}"],check=True,timeout=60)
def tablet_up():
    try: return _ssh("echo ok").stdout.strip()==b"ok"
    except Exception: return False
def push_tablet(pdf_path):
    if not tablet_up(): log("tablet asleep, skipping push (vault note still written)"); return False
    st=STATE/"planner.json"
    try: uuid=json.loads(st.read_text())["uuid"]
    except Exception:
        uuid=_ssh("cat /proc/sys/kernel/random/uuid").stdout.decode().strip()
        st.write_text(json.dumps({"uuid":uuid}))
    XO="/home/root/.local/share/remarkable/xochitl"; ms=str(int(time.time()*1000))
    _scp(pdf_path, f"{XO}/{uuid}.pdf")
    meta=('{"visibleName":"Kodiak Planner","type":"DocumentType","parent":"%s","lastModified":"%s",'
          '"version":0,"deleted":false,"pinned":true,"synced":false,"metadatamodified":true,'
          '"modified":true,"lastOpened":"%s","lastOpenedPage":0}') % (RM_FOLDER_UUID, ms, ms)
    _ssh(f"printf '%s' '{meta}' > {XO}/{uuid}.metadata; echo '{{\"fileType\":\"pdf\"}}' > {XO}/{uuid}.content")
    _ssh("systemctl restart xochitl")
    log("pushed planner to tablet (uuid",uuid[:8]+", xochitl restarted)"); return True

def main():
    if not ICS_URL: raise SystemExit("ICS_URL not set (add op://kdk-ops/proton-calendar-ics/url)")
    from zoneinfo import ZoneInfo
    today=datetime.datetime.now(ZoneInfo(TZ)).date()
    start=datetime.datetime.combine(today, datetime.time(0,0), ZoneInfo(TZ))
    end=start+datetime.timedelta(days=DAYS)
    events=fetch_events(start,end)
    log(f"fetched {len(events)} event(s) for {today} .. +{DAYS}d")
    note=write_note(today,events); log("wrote",note)
    base=render_pdf(today,events, str(STATE/"kodiak-planner"))
    push_tablet(base+".pdf")

if __name__=="__main__":
    try: main()
    except Exception as e:
        msg=f"{type(e).__name__}: {e}"; log("FATAL:",msg); notify("planner-service failed",msg); raise
