#!/usr/bin/env python3
"""papercut_setin.py <fcpxml> <approx_source_start_s> <new_source_start_s>
Move the in-point of the clip whose source start is nearest approx to new (earlier),
extending its duration; re-tile offsets of later clips. fps read from the clip's rational."""
import re,sys
p,approx,new=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
x=open(p).read()
pat=re.compile(r'(<asset-clip[^>]*offset=")(\d+)/(\d+)s("[^>]*duration=")(\d+)/\d+s("[^>]*start=")(\d+)/\d+s(")')
ms=list(pat.finditer(x)); fps=int(ms[0][3])
clips=[(int(m[2]),int(m[5]),int(m[7])) for m in ms]
tgt=min(range(len(clips)),key=lambda i:abs(clips[i][2]/fps-approx))
o,d,s=clips[tgt]; ns=round(new*fps); delta=s-ns
assert delta>0, 'new start must be earlier'
prev_end=max([c[2]+c[1] for c in clips if c[2]<ns]+[0]); assert prev_end<=ns, f'overlaps previous clip ending {prev_end/fps}'
out=[];pos=0
for i,m in enumerate(ms):
    o,d,s=clips[i]
    if i==tgt: d+=delta; s=ns
    elif i>tgt: o+=delta
    out.append(x[pos:m.start()]+f'{m[1]}{o}/{fps}s{m[4]}{d}/{fps}s{m[6]}{s}/{fps}s{m[8]}'); pos=m.end()
out.append(x[pos:]); x=''.join(out)
m=re.search(r'(<sequence[^>]*duration=")(\d+)(/\d+s")',x)
if m: x=x[:m.start()]+f'{m[1]}{int(m[2])+delta}{m[3]}'+x[m.end():]
open(p,'w').write(x); print(f'clip {tgt}: in-point {clips[tgt][2]/fps:.3f} -> {ns/fps:.3f}s (+{delta/fps:.2f}s); {len(clips)} clips')
