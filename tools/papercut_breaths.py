#!/usr/bin/env python3
"""Widen the pause at chosen cue boundaries of a PaperCut FCPXML, using the source's own room tone.

Usage: papercut_breaths.py <video> <fcpxml> <extra_seconds> <t1> [<t2> …]
Each t is a SOURCE time (s) of a boundary (the end of the last word before the cue change). The clip ending
nearest to t gets its out-point extended by extra_seconds (if that source region is silent); if it can't be
(speech follows in the source, e.g. a cut take), the NEXT clip's in-point is moved earlier instead.
Offsets are re-accumulated so the spine still tiles. Prints what it did.
"""
import sys, re, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # repo root: silence.py
import silence
video, xml_path, extra = sys.argv[1], sys.argv[2], float(sys.argv[3]); ts = [float(t) for t in sys.argv[4:]]
x = open(xml_path).read()
FPS = int(re.search(r'frameDuration="1/(\d+)s"', x).group(1)); fr = lambda t: int(round(t * FPS))
raw = silence.detect_silence(video, threshold=0.04, frame_rate=FPS)
clips = [[int(s), int(d)] for s, d in re.findall(r'<asset-clip [^>]*duration="(\d+)/%ds"[^>]*start="(\d+)/%ds"' % (FPS, FPS), x)]
clips = [[s, d] for d, s in clips]          # (start, dur) in frames
quiet = lambda a, b: not raw[a:b].any()
for t in ts:
    tf = fr(t); i = min(range(len(clips)), key=lambda k: abs(clips[k][0] + clips[k][1] - tf))
    s, d = clips[i]; e = s + d; ext = fr(extra)
    nxt = clips[i + 1][0] if i + 1 < len(clips) else len(raw)
    if quiet(e, e + ext) and e + ext <= nxt:
        clips[i][1] += ext; print(f"  {t:7.2f}s: extended clip {i} out by {extra}s (room tone after)")
    elif i + 1 < len(clips) and quiet(nxt - ext, nxt) and nxt - ext >= e:
        clips[i + 1][0] -= ext; clips[i + 1][1] += ext; print(f"  {t:7.2f}s: moved clip {i+1} in earlier by {extra}s (room tone before)")
    else: print(f"  {t:7.2f}s: no silent room tone available on either side — left as is")
# rewrite the spine with new start/duration/offset
items = list(re.finditer(r'<asset-clip ([^>]*)(/>|>)', x)); out = []; pos = 0; tl = 0
for k, m in enumerate(items):
    attrs = m.group(1); s, d = clips[k]
    attrs = re.sub(r'offset="[^"]*"', f'offset="{tl}/{FPS}s"', attrs); attrs = re.sub(r'duration="[^"]*"', f'duration="{d}/{FPS}s"', attrs); attrs = re.sub(r'start="[^"]*"', f'start="{s}/{FPS}s"', attrs)
    out.append(x[pos:m.start()] + "<asset-clip " + attrs + m.group(2)); pos = m.end(); tl += d
out.append(x[pos:]); open(xml_path, "w").write("".join(out)); print(f"rewrote {xml_path}: {len(clips)} clips, {tl/FPS:.1f}s")
