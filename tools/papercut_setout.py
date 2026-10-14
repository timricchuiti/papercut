#!/usr/bin/env python3
"""papercut_setout.py <fcpxml> <approx_source_end_s> <new_source_end_s>
Extend (or trim) the out-point of the clip whose source END is nearest approx; re-tile later clips."""
import re,sys
p,approx,new=sys.argv[1],float(sys.argv[2]),float(sys.argv[3])
x=open(p).read()
pat=re.compile(r'(<asset-clip[^>]*offset=")(\d+)/(\d+)s("[^>]*duration=")(\d+)/\d+s("[^>]*start=")(\d+)/\d+s(")')
ms=list(pat.finditer(x)); fps=int(ms[0][3])
clips=[(int(m[2]),int(m[5]),int(m[7])) for m in ms]
tgt=min(range(len(clips)),key=lambda i:abs((clips[i][2]+clips[i][1])/fps-approx))
o,d,s=clips[tgt]; nd=round(new*fps)-s; delta=nd-d
assert nd>0
nxt=[c[2] for c in clips if c[2]>s]
if nxt: assert s+nd<=min(nxt), f'would overlap the clip starting {min(nxt)/fps} in source'
out=[];pos=0
for i,m in enumerate(ms):
    o,d,s=clips[i]
    if i==tgt: d=nd
    elif i>tgt: o+=delta
    out.append(x[pos:m.start()]+f'{m[1]}{o}/{fps}s{m[4]}{d}/{fps}s{m[6]}{s}/{fps}s{m[8]}'); pos=m.end()
out.append(x[pos:]); x=''.join(out)
m=re.search(r'(<sequence[^>]*duration=")(\d+)(/\d+s")',x)
if m: x=x[:m.start()]+f'{m[1]}{int(m[2])+delta}{m[3]}'+x[m.end():]
open(p,'w').write(x); print(f'clip {tgt}: out-point {(clips[tgt][2]+clips[tgt][1])/fps:.3f} -> {(clips[tgt][2]+nd)/fps:.3f}s ({delta/fps:+.2f}s); {len(clips)} clips')
