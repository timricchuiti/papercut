#!/usr/bin/env python3
"""papercut_addclip.py <fcpxml> <src_start_s> <src_end_s>
Insert a new clip of the same media covering SOURCE [start, end] into a PaperCut FCPXML, placed in
source order among the existing clips (before the first clip whose source start is later), then re-tile
offsets and fix the sequence duration. For speech the exporter dropped because the transcript put no
block there (e.g. MLX timestamped a line into a silence) and setin/setout can't reach it."""
import re, sys
p, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]); x = open(p).read()
den = int(re.search(r'frameDuration="1/(\d+)s"', x).group(1))
items = list(re.finditer(r'<asset-clip ([^>]*?)(/>|>.*?</asset-clip>)', x, re.S))
head, tail = x[:items[0].start()], x[items[-1].end():]
sa, dur = round(a * den), round((b - a) * den); assert dur > 0
clips = [m.group(0) for m in items]
starts = [int(re.search(r'start="(\d+)/', c).group(1)) for c in clips]
for s, d in zip(starts, [int(re.search(r'duration="(\d+)/', c).group(1)) for c in clips]):
    assert not (s < sa + dur and sa < s + d), f'overlaps existing clip at source {s/den:.2f}-{(s+d)/den:.2f}s'
tmpl = re.sub(r'/?>.*', '/>', clips[0], flags=re.S)                      # strip any inner markers
new = re.sub(r'start="[^"]*"', f'start="{sa}/{den}s"', re.sub(r'duration="[^"]*"', f'duration="{dur}/{den}s"', tmpl))
idx = next((i for i, s in enumerate(starts) if s > sa), len(clips)); clips.insert(idx, new)
out, tl = [], 0
for c in clips:
    out.append(re.sub(r'offset="[^"]*"', f'offset="{tl}/{den}s"', c, count=1)); tl += int(re.search(r'duration="(\d+)/', c).group(1))
x = head + "\n".join(k.strip("\n") for k in out) + tail
x = re.sub(r'(<sequence[^>]*duration=")\d+(/\d+s")', lambda m: f'{m[1]}{tl}{m[2]}', x, count=1)
open(p, "w").write(x); print(f"inserted clip {idx}: source {a:.2f}-{b:.2f}s ({dur}f); {len(clips)} clips, {tl/den:.1f}s")
