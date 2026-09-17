from kodiak_lib import *
s=SVG()
s.header("MEETING NOTES","DATE")
# top fields
s.text(ML,250,"TITLE",24,fill=MUTED,weight="bold",spacing="1"); s.line(ML+120,250,MR,250,sw=2)
s.text(ML,322,"ATTENDEES",24,fill=MUTED,weight="bold",spacing="1"); s.line(ML+210,322,MR,322,sw=2)
s.line(ML,372,MR,372,stroke=DIV,sw=2)
# columns
COLX=1024
s.line(COLX-24,410,COLX-24,H-70,stroke=DIV,sw=2)   # vertical divider
# left: discussion (lines)
s.label(ML,452,"DISCUSSION")
y=522
while y<H-70:
    s.line(ML,y,COLX-56,y,stroke=LINE,sw=2); y+=60
# right: action items (checkbox rows) then decisions
s.label(COLX,452,"ACTION ITEMS")
y=520
for i in range(9):
    s.checkbox(COLX,y-32,40); s.line(COLX+60,y,MR,y,stroke=LINE,sw=2); y+=72
s.label(COLX,y+34,"DECISIONS"); y+=90
while y<H-70:
    s.line(COLX,y,MR,y,stroke=DIV,sw=2); y+=58
s.save("kodiak-meeting")
print("built kodiak-meeting")
