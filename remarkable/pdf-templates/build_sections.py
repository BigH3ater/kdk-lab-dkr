from PIL import Image, ImageDraw, ImageFont
import cairosvg
from pypdf import PdfWriter, PdfReader
import os
W,H=1620,2160
BLUE=(78,105,201); TERRA=(192,118,79); WHITE=(255,255,255)
FB="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
COV=os.environ["COVDIR"]
def cover_fill(im,fx=0.5,fy=0.5):
    s=max(W/im.width,H/im.height); nw,nh=int(im.width*s),int(im.height*s)
    im=im.resize((nw,nh),Image.LANCZOS)
    x=int((nw-W)*fx); y=int((nh-H)*fy)
    return im.crop((x,y,x+W,y+H))
def spaced(draw,xy,text,font,fill,ls):
    x,y=xy
    for ch in text: draw.text((x,y),ch,font=font,fill=fill); x+=draw.textlength(ch,font=font)+ls
def spaced_w(draw,text,font,ls): return sum(draw.textlength(c,font=font)+ls for c in text)-ls
def cover(title,img,out,fx=0.5,fy=0.5):
    base=cover_fill(Image.open(f"{COV}/{img}").convert("RGB"),fx,fy).convert("RGBA")
    ov=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(ov)
    for y in range(980):
        a=int(205*(1-y/980)**1.3); d.line([(0,y),(W,y)],fill=(*BLUE,a))
    for y in range(H-360,H):
        a=int(90*((y-(H-360))/360)); d.line([(0,y),(W,y)],fill=(20,24,32,a))
    img2=Image.alpha_composite(base,ov); d=ImageDraw.Draw(img2)
    fw=ImageFont.truetype(FB,34); ft=ImageFont.truetype(FB,132)
    wm="KODIAK  ·  CODEX"; ww=spaced_w(d,wm,fw,10); spaced(d,((W-ww)//2,150),wm,fw,WHITE,10)
    ty=300
    for ln in title.split("\n"):
        tw=d.textlength(ln,font=ft); d.text(((W-tw)//2,ty),ln,font=ft,fill=WHITE); ty+=150
    d.rectangle([W//2-150,ty+6,W//2+150,ty+14],fill=TERRA)
    img2.convert("RGB").save(out,"PDF",resolution=229.0)
    img2.convert("RGB").resize((540,720)).save(out.replace(".pdf",".png"))

# dark lined writing page (true-black ground + light-gray filled rules)
def dark_lined(out):
    e=['<rect x="0" y="0" width="1620" height="2160" fill="#000000"/>']
    y=150
    while y<2100:
        e.append(f'<rect x="96" y="{y}" width="1428" height="2.5" fill="#AAAAAA"/>'); y+=62
    e.append('<rect x="300" y="0" width="3" height="2160" fill="#D0D0D0"/>')  # margin
    svg='<svg xmlns="http://www.w3.org/2000/svg" width="1620" height="2160" viewBox="0 0 1620 2160">'+"".join(e)+"</svg>"
    cairosvg.svg2pdf(bytestring=svg.encode(),write_to=out)
dark_lined("sections/_lined.pdf")

SECTIONS=[
 ("INBOX","aurora.jpg",0.5,0.4),
 ("HOMELAB","mountain.jpg",0.5,0.5),
 ("PERSONAL","scenery.jpg",0.5,0.5),
 ("BOOKS","redpeak.jpg",0.5,0.5),
 ("DEVELOPMENT","mountain.jpg",0.18,0.2),   # distinct crop
 ("LEARNING","scenery.jpg",0.85,0.7),        # distinct crop
]
PAGES=8
for name,img,fx,fy in SECTIONS:
    cover(name,img,f"sections/_cover_{name}.pdf",fx,fy)
    wr=PdfWriter(); wr.append(PdfReader(f"sections/_cover_{name}.pdf"))
    for _ in range(PAGES): wr.append(PdfReader("sections/_lined.pdf"))
    outn=f"sections/notebook-{name.title()}.pdf"
    with open(outn,"wb") as f: wr.write(f)
    print("built",outn,"pages=",1+PAGES)
