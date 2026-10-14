#!/usr/bin/env python3
"""papercut_moveclips.py <fcpxml> <src_lo> <src_hi> <after_src_time>
Move every clip whose SOURCE start lies in [src_lo, src_hi] so the group sits right after the clip
that contains (or ends nearest) after_src_time. Re-tiles offsets. Markers travel with their clips."""
import re,sys
p,lo,hi,after=sys.argv[1],float(sys.argv[2]),float(sys.argv[3]),float(sys.argv[4])
x=open(p).read()
pat=re.compile(r'<asset-clip\b[^>]*offset="(\d+)/(\d+)s"[^>]*duration="(\d+)/\d+s"[^>]*start="(\d+)/\d+s"[^>]*?(?:/>|>.*?</asset-clip>)',re.S)
ms=list(pat.finditer(x)); fps=int(ms[0][2])
items=[{'txt':m.group(0),'off':int(m[1]),'dur':int(m[3]),'st':int(m[4]),'span':(m.start(),m.end())} for m in ms]
mov=[c for c in items if lo<=c['st']/fps<=hi]; rest=[c for c in items if not (lo<=c['st']/fps<=hi)]
assert mov, 'no clips in range'
anchor=min(range(len(rest)),key=lambda i:abs((rest[i]['st']+rest[i]['dur'])/fps-after))
order=rest[:anchor+1]+mov+rest[anchor+1:]
off=0;new=[]
for c in order:
    t=re.sub(r'offset="\d+/\d+s"',f'offset="{off}/{fps}s"',c['txt'],count=1); new.append(t); off+=c['dur']
head=x[:items[0]['span'][0]]; tail=x[items[-1]['span'][1]:]
# preserve whatever sat between clips (normally just whitespace/newlines)
seps=[x[items[i]['span'][1]:items[i+1]['span'][0]] for i in range(len(items)-1)]
sep=max(set(seps),key=seps.count) if seps else '\n'
open(p,'w').write(head+sep.join(new)+tail)
print(f'moved {len(mov)} clips (source {mov[0]["st"]/fps:.2f}-{(mov[-1]["st"]+mov[-1]["dur"])/fps:.2f}) after clip ending {(rest[anchor]["st"]+rest[anchor]["dur"])/fps:.2f}; total {off/fps:.1f}s')
