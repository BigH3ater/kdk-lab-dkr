from kodiak_lib import *
s=SVG()
s.header("TO-DO","DATE")
y=278; s.label(ML,y,"TOP PRIORITIES")
py=y+64
for i in range(3):
    yy=py+i*78; s.checkbox(ML,yy-40,48,stroke=TERRA); s.line(ML+74,yy,MR,yy,sw=2)
y=py+3*78+40; s.label(ML,y,"TASKS")
ty=y+62
for i in range(13):
    yy=ty+i*70; s.checkbox(ML,yy-40,44); s.line(ML+68,yy,MR,yy,sw=2)
y=ty+13*70+30; s.label(ML,y,"NOTES")
ny=y+70
while ny<H-70:
    s.line(ML,ny,MR,ny,stroke=DIV,sw=2); ny+=64
s.save("kodiak-todo")
print("built todo",SUFFIX or "(light)")
