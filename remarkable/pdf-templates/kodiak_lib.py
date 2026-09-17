"""Kodiak Codex template brand kit: palette + SVG primitives + save helpers.
Paper Pro page 1620x2160 (3:4). Native templates are grayscale-only, so color
templates are PDFs; keep colour to muted accents (Templacity guidance)."""
import cairosvg
W,H = 1620,2160
ML,MR = 96,1524
BLUE="#4E69C9"; TERRA="#C0764F"; GRAY="#9AA0A6"
LINE="#C9CDD2"; DIV="#E4E6E9"; TEXT="#2B2F33"; MUTED="#6B7076"; BG="#FCFBF8"; WHITE="#FFFFFF"
FONT="DejaVu Sans, Helvetica, sans-serif"
def esc(s): return s.replace("&","&amp;").replace("<","&lt;")
class SVG:
    def __init__(s): s.e=[]; s.rect(0,0,W,H,fill=BG)
    def rect(s,x,y,w,h,fill=None,stroke=None,sw=0,rx=0):
        a=f'<rect x="{x}" y="{y}" width="{w}" height="{h}"'
        a+=f' rx="{rx}" ry="{rx}"' if rx else ''
        a+=f' fill="{fill}"' if fill else ' fill="none"'
        a+=f' stroke="{stroke}" stroke-width="{sw}"' if stroke else ''
        s.e.append(a+"/>")
    def line(s,x1,y1,x2,y2,stroke=LINE,sw=2,dash=None):
        d=f' stroke-dasharray="{dash}"' if dash else ''
        s.e.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{stroke}" stroke-width="{sw}"{d}/>')
    def text(s,x,y,t,size,fill=TEXT,weight="normal",anchor="start",spacing=None):
        ls=f' letter-spacing="{spacing}"' if spacing else ''
        s.e.append(f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
                   f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}>{esc(t)}</text>')
    def label(s,x,y,t):            # section label: blue caps + terracotta underline
        s.text(x,y,t,32,fill=BLUE,weight="bold",spacing="2")
        s.line(x,y+16,x+16+len(t)*17,y+16,stroke=TERRA,sw=4)
    def checkbox(s,x,y,size=42,stroke=GRAY):
        s.rect(x,y,size,size,fill=WHITE,stroke=stroke,sw=3,rx=8)
    def header(s,title,right="DATE"):
        s.rect(0,0,W,176,fill=BLUE)
        s.text(ML,116,title,52,fill=WHITE,weight="bold",spacing="3")
        s.text(MR,96,right,26,fill="#DFE4F5",weight="bold",anchor="end",spacing="2")
        s.line(1230,120,MR,120,stroke="#AEBEEA",sw=3)
    def save(s,base):
        svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">\n'+"\n".join(s.e)+"\n</svg>"
        open(base+".svg","w").write(svg)
        cairosvg.svg2pdf(bytestring=svg.encode(), write_to=base+".pdf")
        cairosvg.svg2png(bytestring=svg.encode(), write_to=base+".png", output_width=810)
        return base

def poly(pts,fill,opacity=1):
    p=" ".join(f"{x},{y}" for x,y in pts)
    o=f' fill-opacity="{opacity}"' if opacity!=1 else ''
    return f'<polygon points="{p}" fill="{fill}"{o}/>'
def circle(cx,cy,r,fill,opacity=1):
    o=f' fill-opacity="{opacity}"' if opacity!=1 else ''
    return f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"{o}/>'
