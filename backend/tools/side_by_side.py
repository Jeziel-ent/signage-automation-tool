import json,sys
from pathlib import Path
from PIL import Image
sys.stdout.reconfigure(encoding="utf-8")
res=json.load(open(sys.argv[1],encoding='utf-8'))
snos={int(x) for x in sys.argv[3].split(',')} if len(sys.argv)>3 else None
rows=[]
for r in res:
    if r.get('png') and r.get('ref') and (not snos or r['sno'] in snos):
        a=Image.open(r['ref']).convert('RGB');b=Image.open(r['png']).convert('RGB');h=340
        a=a.resize((int(a.width*h/a.height),h));b=b.resize((int(b.width*h/b.height),h))
        im=Image.new('RGB',(a.width+b.width+16,h),'white');im.paste(a,(0,0));im.paste(b,(a.width+16,0));rows.append(im)
W=max(i.width for i in rows);s=Image.new('RGB',(W,sum(i.height+8 for i in rows)),'gray');y=0
for i in rows:s.paste(i,(0,y));y+=i.height+8
s.thumbnail((1700,2200));s.save(sys.argv[2])
