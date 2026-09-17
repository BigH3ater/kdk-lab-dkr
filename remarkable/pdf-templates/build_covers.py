from kodiak_lib import *
BLUE_LT="#9BB0E6"; TERRA_LT="#D9A585"

def cover(title, base, accent=TERRA):
    s=SVG()
    # brand wordmark
    s.text(W/2,300,"KODIAK  ·  CODEX",30,fill=BLUE,weight="bold",anchor="middle",spacing="10")
    # title (large, wraps to two lines if needed)
    lines=title.split("\n")
    ty=560 if len(lines)==1 else 500
    for ln in lines:
        s.text(W/2,ty,ln,120,fill=TEXT,weight="bold",anchor="middle",spacing="2"); ty+=140
    # terracotta rule under title
    s.line(W/2-150,ty-40,W/2+150,ty-40,stroke=accent,sw=6)
    # --- Alaska mountain motif (original vector), layered back->front ---
    s.e.append(circle(W/2+120,1230,190,TERRA_LT,0.55))                # muted sun
    s.e.append(poly([(0,1560),(360,1180),(720,1520),(1040,1160),(1360,1520),(1620,1300),(1620,2160),(0,2160)],BLUE_LT,0.9))  # back range
    s.e.append(poly([(0,1760),(300,1440),(640,1740),(980,1420),(1300,1760),(1620,1560),(1620,2160),(0,2160)],BLUE,1))        # mid range
    s.e.append(poly([(0,1980),(420,1740),(820,1980),(1200,1720),(1620,1980),(1620,2160),(0,2160)],TERRA,1))                  # foreground hills
    # snow caps on the mid range (cream accents)
    for px,py in [(980,1420)]:
        s.e.append(poly([(px,py),(px-58,py+128),(px+58,py+128)],BG,0.95))
    s.save(base); print("cover:",base)

cover("TO-DO","cover-todo")
cover("MEETING\nNOTES","cover-meeting")
cover("CALENDAR","cover-calendar")
cover("KODIAK\nCODEX","cover-kodiak", accent=BLUE)
