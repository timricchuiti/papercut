#!/usr/bin/env python3
"""papercut_audiodelay.py <in.fcpxml> <out.fcpxml> <frames>
Write a copy of a PaperCut FCPXML in which every spine clip's audio plays <frames> LATER than its
picture (positive = delay audio; use when the source's audio runs ahead of the video in FCP).
Each <asset-clip> becomes video-only (srcEnable="video", plus a -96 dB fallback mute) carrying a
lane -1 child asset-clip of the SAME source window, srcEnable="audio", placed <frames> later.
The audio windows still tile edge to edge, so every cut keeps exactly the audio it had; the audio
of the last clip is trimmed by <frames> so the project still ends on the last picture frame.
The project is renamed to the output file's stem so it imports as a separate FCP project."""
import os,re,sys
src,dst,fr=sys.argv[1],sys.argv[2],int(sys.argv[3])
x=open(src).read()
fps=int(re.search(r'frameDuration="1/(\d+)s"',x)[1])
pat=re.compile(r'<asset-clip( [^>]*?offset="(\d+)/(\d+)s"[^>]*?duration="(\d+)/\d+s"[^>]*?start="(\d+)/\d+s"[^>]*?)(/?)>')
ms=[m for m in pat.finditer(x) if 'lane=' not in m[1]]
assert ms and all(int(m[3])==fps for m in ms)
out=[];pos=0
for i,m in enumerate(ms):
    attrs,d,s=m[1],int(m[4]),int(m[5])
    name=re.search(r'name="([^"]*)"',attrs)[1]; ref=re.search(r'ref="([^"]*)"',attrs)[1]
    cd=d-fr if i==len(ms)-1 else d
    assert cd>0
    child=(f'<adjust-volume amount="-96dB"/>'
           f'<asset-clip name="{name}" ref="{ref}" lane="-1" offset="{s+fr}/{fps}s" duration="{cd}/{fps}s" '
           f'start="{s}/{fps}s" srcEnable="audio"/>')
    head=f'<asset-clip{attrs} srcEnable="video">'+child
    out.append(x[pos:m.start()]+head+('</asset-clip>' if m[6] else '')); pos=m.end()
out.append(x[pos:]); x=''.join(out)
stem=os.path.splitext(os.path.basename(dst))[0]
x=re.sub(r'(<project name=")[^"]*(")',lambda m:m[1]+stem+m[2],x,count=1)
open(dst,'w').write(x)
print(f'{len(ms)} clips: audio delayed {fr} frames ({fr/fps:.4f}s); project "{stem}" -> {dst}')
