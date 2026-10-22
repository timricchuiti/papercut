#!/usr/bin/env python3
"""papercut_setin.py <fcpxml> <approx_source_start_s> <new_source_start_s>
Move the in-point of the clip whose source START is nearest approx — earlier (recover a
clipped onset) or later (trim a cut word's tail that bled into the next clip) — and
re-tile later clips. Mirrors papercut_setout.py."""
import re,sys
p,approx,new=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
x=open(p).read()
pat=re.compile(r'(<asset-clip[^>]*offset=")(\d+)/(\d+)s("[^>]*duration=")(\d+)/\d+s("[^>]*start=")(\d+)/\d+s(")')
ms=list(pat.finditer(x)); fps=int(ms[0][3])
clips=[(int(m[2]),int(m[5]),int(m[7])) for m in ms]
tgt=min(range(len(clips)),key=lambda i:abs(clips[i][2]/fps-approx))
o,d,s=clips[tgt]; ns=round(new*fps); delta=s-ns; nd=d+delta
assert nd>0, 'new start is at or past the clip end'
if delta>0:
    prev_end=max([c[2]+c[1] for c in clips if c[2]<s]+[0]); assert prev_end<=ns, f'overlaps previous clip ending {prev_end/fps}'
out=[];pos=0
for i,m in enumerate(ms):
    oo,dd,ss=clips[i]
    if i==tgt: dd,ss=nd,ns
    elif i>tgt: oo+=delta
    out.append(x[pos:m.start()]+f'{m[1]}{oo}/{fps}s{m[4]}{dd}/{fps}s{m[6]}{ss}/{fps}s{m[8]}'); pos=m.end()
out.append(x[pos:]); x=''.join(out)
m=re.search(r'(<sequence[^>]*duration=")(\d+)(/\d+s")',x)
if m: x=x[:m.start()]+f'{m[1]}{int(m[2])+delta}{m[3]}'+x[m.end():]
open(p,'w').write(x); print(f'clip {tgt}: in-point {s/fps:.3f} -> {ns/fps:.3f}s ({-delta/fps:+.2f}s), duration {d/fps:.2f} -> {nd/fps:.2f}s; {len(clips)} clips')
