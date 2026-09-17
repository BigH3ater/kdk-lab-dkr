from PIL import Image, ImageDraw, ImageFont
W,H=1620,2160
BLUE=(78,105,201); TERRA=(192,118,79); WHITE=(255,255,255)
FB="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SRC="assets/barometer-mountain.jpg"
def cover_fill(im):
    s=max(W/im.width,H/im.height); nw,nh=int(im.width*s),int(im.height*s)
    im=im.resize((nw,nh),Image.LANCZOS)
    return im.crop(((nw-W)//2,(nh-H)//2,(nw-W)//2+W,(nh-H)//2+H))
def spaced(draw,xy,text,font,fill,ls):
    x,y=xy
    for ch in text:
        draw.text((x,y),ch,font=font,fill=fill); x+=draw.textlength(ch,font=font)+ls
def spaced_w(draw,text,font,ls):
    return sum(draw.textlength(ch,font=font)+ls for ch in text)-ls
def make(title_lines, out):
    base=cover_fill(Image.open(SRC).convert("RGB")).convert("RGBA")
    ov=Image.new("RGBA",(W,H),(0,0,0,0)); d=ImageDraw.Draw(ov)
    # top brand-blue gradient for title legibility
    top_h=980
    for y in range(top_h):
        a=int(205*(1-y/top_h)**1.3)
        d.line([(0,y),(W,y)],fill=(BLUE[0],BLUE[1],BLUE[2],a))
    # subtle bottom darkening for depth
    for y in range(H-360,H):
        a=int(90*((y-(H-360))/360))
        d.line([(0,y),(W,y)],fill=(20,24,32,a))
    img=Image.alpha_composite(base,ov)
    d=ImageDraw.Draw(img)
    fword=ImageFont.truetype(FB,34); ftitle=ImageFont.truetype(FB,132)
    # wordmark centered
    wm="KODIAK  ·  CODEX"; ww=spaced_w(d,wm,fword,10)
    spaced(d,((W-ww)//2,150),wm,fword,WHITE,10)
    # title (1-2 lines) centered
    ty=300
    for ln in title_lines:
        tw=d.textlength(ln,font=ftitle); d.text(((W-tw)//2,ty),ln,font=ftitle,fill=WHITE); ty+=150
    # terracotta rule
    d.rectangle([W//2-150,ty+6,W//2+150,ty+14],fill=TERRA)
    img.convert("RGB").save(out+".png")
    img.convert("RGB").save(out+".pdf","PDF",resolution=229.0)
    print("photo cover:",out)
make(["KODIAK","CODEX"],"cover-photo-kodiak")
make(["TO-DO"],"cover-photo-todo")
make(["MEETING","NOTES"],"cover-photo-meeting")
make(["CALENDAR"],"cover-photo-calendar")
