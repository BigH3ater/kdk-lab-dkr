#!/usr/bin/env python3
"""Build the Kodiak To-Do color PDF for the reMarkable Paper Pro.
Native templates are grayscale-only, so a COLOR template must be a PDF (imported
as a document, optionally 'promote to template' on-device). Muted accents only
(Templacity: saturated tints look wrong on Canvas). Palette: Blue #4E69C9 (matches
the recommended ink) + muted terracotta #C0764F; light grays for lines.
SVG -> PDF (+PNG preview) via cairosvg. Page 1620x2160 (Paper Pro 3:4)."""
import cairosvg
W,H = 1620,2160
BLUE="#4E69C9"; BLUE_SOFT="#5B79C0"; TERRA="#C0764F"
GRAY="#9AA0A6"; LINE="#C9CDD2"; DIV="#E4E6E9"; TEXT="#2B2F33"; WHITE="#FFFFFF"
ML,MR = 96,1524                      # content margins
def esc(s): return s.replace("&","&amp;")
e=[]
def rect(x,y,w,h,fill=None,stroke=None,sw=0,rx=0):
    a=f'<rect x="{x}" y="{y}" width="{w}" height="{h}"'
    if rx: a+=f' rx="{rx}" ry="{rx}"'
    if fill: a+=f' fill="{fill}"'
    else: a+=' fill="none"'
    if stroke: a+=f' stroke="{stroke}" stroke-width="{sw}"'
    e.append(a+"/>")
def line(x1,y1,x2,y2,stroke=LINE,sw=2,dash=None):
    d=f' stroke-dasharray="{dash}"' if dash else ""
    e.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"{d}/>')
def text(x,y,s,size,fill=TEXT,weight="normal",anchor="start",spacing=None):
    ls=f' letter-spacing="{spacing}"' if spacing else ""
    e.append(f'<text x="{x}" y="{y}" font-family="DejaVu Sans, Helvetica, sans-serif" '
             f'font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}>{esc(s)}</text>')
def checkbox(x,y,s=44,stroke=GRAY):
    rect(x,y,s,s,fill=WHITE,stroke=stroke,sw=3,rx=9)

# background (warm near-white, very subtle -- not a saturated bg)
rect(0,0,W,H,fill="#FCFBF8")
# header band
rect(0,0,W,176,fill=BLUE)
text(ML,116,"TO-DO",58,fill=WHITE,weight="bold",spacing="3")
text(1524,96,"DATE",26,fill="#Dfe4f5",weight="bold",anchor="end",spacing="2")
line(1230,120,1524,120,stroke="#AEBEEA",sw=3)
# --- TOP PRIORITIES ---
y=278
text(ML,y,"TOP PRIORITIES",34,fill=BLUE,weight="bold",spacing="2")
line(ML,y+18,ML+300,y+18,stroke=TERRA,sw=4)
py=y+64
for i in range(3):
    yy=py+i*78
    checkbox(ML,yy-38,48,stroke=TERRA)
    line(ML+72,yy,MR,yy,stroke=LINE,sw=2)
# --- TASKS ---
y=py+3*78+40
text(ML,y,"TASKS",34,fill=BLUE,weight="bold",spacing="2")
line(ML,y+18,ML+140,y+18,stroke=TERRA,sw=4)
ty=y+62
rows=13; sp=70
for i in range(rows):
    yy=ty+i*sp
    checkbox(ML,yy-40,44,stroke=GRAY)
    line(ML+68,yy,MR,yy,stroke=LINE,sw=2)
# --- NOTES ---
y=ty+rows*sp+30
text(ML,y,"NOTES",34,fill=BLUE,weight="bold",spacing="2")
line(ML,y+18,ML+150,y+18,stroke=TERRA,sw=4)
ny=y+70
while ny < H-70:
    line(ML,ny,MR,ny,stroke=DIV,sw=2)
    ny+=64

svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n'+"\n".join(e)+"\n</svg>"
open("remarkable/pdf-templates/kodiak-todo.svg","w").write(svg)
cairosvg.svg2pdf(bytestring=svg.encode(), write_to="remarkable/pdf-templates/kodiak-todo.pdf")
cairosvg.svg2png(bytestring=svg.encode(), write_to="remarkable/pdf-templates/kodiak-todo.png", output_width=810)
print("built kodiak-todo.svg/.pdf/.png")
