from kodiak_lib import *
from pypdf import PdfWriter, PdfReader

# ---- Page 1: MONTH grid ----
def month_page():
    s=SVG()
    s.header("CALENDAR","")
    s.text(ML,250,"MONTH",24,fill=MUTED,weight="bold",spacing="1"); s.line(ML+120,250,760,250,sw=2)
    s.text(820,250,"YEAR",24,fill=MUTED,weight="bold",spacing="1"); s.line(940,250,MR,250,sw=2)
    days=["SUN","MON","TUE","WED","THU","FRI","SAT"]
    gx0,gy0=ML,320; gw=(MR-ML); cw=gw/7; rows=6; ch=(H-70-gy0-60)/rows
    # day-of-week header
    for i,d in enumerate(days):
        s.text(gx0+i*cw+16,gy0+34,d,26,fill=BLUELBL,weight="bold",spacing="1")
    s.line(gx0,gy0+56,MR,gy0+56,stroke=TERRA,sw=4)
    top=gy0+56
    # cells
    for r in range(rows):
        for c in range(7):
            x=gx0+c*cw; y=top+r*ch
            s.rect(x,y,cw,ch,stroke=DIV,sw=2)
            s.rect(x+10,y+10,46,40,fill=BG,stroke=LINE,sw=2,rx=7)  # date box
    return s.save("cal-month")+".pdf"

# ---- Page 2: WEEK view ----
def week_page():
    s=SVG()
    s.header("WEEK","")
    s.text(ML,250,"WEEK OF",24,fill=MUTED,weight="bold",spacing="1"); s.line(ML+180,250,MR,250,sw=2)
    days=["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"]
    colx=1120                                  # right focus column divider
    s.line(colx-24,300,colx-24,H-70,stroke=DIV,sw=2)
    y0=320; rh=(H-70-y0)/7
    for i,d in enumerate(days):
        y=y0+i*rh
        s.text(ML,y+40,d,26,fill=BLUELBL,weight="bold",spacing="1")
        # ruled lines within each day (left area)
        yy=y+70
        while yy<y+rh-14:
            s.line(ML,yy,colx-56,yy,stroke=LINE,sw=2); yy+=52
        s.line(ML,y+rh,colx-56,y+rh,stroke=DIV,sw=2)
    # right: focus / to-do
    s.label(colx,y0+34,"THIS WEEK")
    yy=y0+96
    for i in range(12):
        s.checkbox(colx,yy-32,40); s.line(colx+60,yy,MR,yy,stroke=LINE,sw=2); yy+=78
    return s.save("cal-week")+".pdf"

m=month_page(); w=week_page()
wr=PdfWriter()
for p in (m,w): wr.append(PdfReader(p))
with open(f"kodiak-calendar{SUFFIX}.pdf","wb") as f: wr.write(f)
print("built kodiak-calendar.pdf (month+week)")
