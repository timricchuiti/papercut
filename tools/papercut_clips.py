#!/usr/bin/env python3
"""papercut_clips.py <fcpxml> [lo_src_s hi_src_s]
List a PaperCut FCPXML's clips: timeline pos, source in/out, duration, gap to the previous clip
(in source seconds), markers; final line reports clip count, whether offsets tile, total, markers.
Optional lo/hi restrict the listing to clips touching that SOURCE range."""
import re,sys
x=open(sys.argv[1]).read()
fps=None
rows=[]
for m in re.finditer(r'<asset-clip\b[^>]*?(?:/>|>.*?</asset-clip>)',x,re.S):
    t=m.group(0); a=re.search(r'offset="(\d+)/(\d+)s"[^>]*duration="(\d+)/\d+s"[^>]*start="(\d+)/\d+s"',t)
    fps=int(a[2]); o,d,s=int(a[1]),int(a[3]),int(a[4])
    mk=re.findall(r'<marker[^>]*value="([^"]*)"',t)
    rows.append((o/fps,d/fps,s/fps,mk))
def mm(t): return f"{int(t//60)}:{t%60:05.2f}"
lo=float(sys.argv[2]) if len(sys.argv)>2 else 0; hi=float(sys.argv[3]) if len(sys.argv)>3 else 1e9
ok=True
for i,(o,d,s,mk) in enumerate(rows):
    if i and abs(rows[i-1][0]+rows[i-1][1]-o)>1e-6: ok=False
    if s+d>=lo and s<=hi:
        gap='' if i==0 else f" gap {s-(rows[i-1][2]+rows[i-1][1]):6.2f}"
        print(f"{i:3d} tl {mm(o)} src {mm(s)}-{mm(s+d)} ({d:5.2f}s){gap}{'  M: '+' | '.join(mk) if mk else ''}")
print(f"clips={len(rows)} tiles={ok} total={mm(sum(r[1] for r in rows))} markers={sum(len(r[3]) for r in rows)}")
