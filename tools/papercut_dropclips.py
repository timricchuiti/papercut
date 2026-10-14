#!/usr/bin/env python3
"""Drop every clip of a PaperCut FCPXML whose SOURCE start lies in [lo, hi] seconds, then re-tile offsets.
Usage: papercut_dropclips.py <fcpxml> <lo> <hi>   (fps-agnostic; prints what it dropped)"""
import sys, re
p, lo, hi = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]); x = open(p).read()
items = list(re.finditer(r'<asset-clip ([^>]*?)(/>|>.*?</asset-clip>)', x, re.S)); keep = []; tl = 0
den = int(re.search(r'frameDuration="1/(\d+)s"', x).group(1))
head, tail = x[:items[0].start()], x[items[-1].end():]
for m in items:
    a = m.group(1); s = int(re.search(r'start="(\d+)/', a).group(1)) / den; d = int(re.search(r'duration="(\d+)/', a).group(1))
    if lo <= s <= hi: print(f"  dropped clip at source {s:.2f}s ({d}f)"); continue
    keep.append(re.sub(r'offset="[^"]*"', f'offset="{tl}/{den}s"', m.group(0), count=1)); tl += d
open(p, "w").write(head + "\n".join(k.strip("\n") for k in keep) + tail); print(f"rewrote {p}: {len(keep)} clips, {tl/den:.1f}s")
