#!/usr/bin/env python3
"""papercut_edgescan.py <fcpxml> <whisper.json> <media> [--thr 0.01] [--short 0.7]

Post-export sanity scan of a PaperCut FCPXML against the source audio. Reports:

  1. SHORT clips (< --short s) and the words they contain. A short clip with NO words
     is a noise fragment (a mis-timed MLX word placed over a cough / click / off-mic
     noise) -> drop it with papercut_dropclips.py.
  2. Sound just OUTSIDE a clip edge (within 0.5 s, RMS over --thr) that is NOT
     explained by a cut word sitting in the gap. That is a transcriber timing error:
     an early word END (clipped tail) or late word START (clipped onset) -> fix with
     papercut_setout.py / papercut_setin.py after a look at the waveform.

RMS is computed from the media with ffmpeg (16 kHz mono, 50 ms frames); no sidecar
needed. Word timings come from the MLX/WhisperX JSON; zero-width ghost words and
pause-stretched words (> 1.2 s) are ignored when deciding whether a gap "has words".
"""
import json
import re
import subprocess
import sys

import numpy as np

args = sys.argv[1:]
opts = {"thr": 0.01, "short": 0.7}
pos = []
i = 0
while i < len(args):
    if args[i] in ("--thr", "--short"):
        opts[args[i][2:]] = float(args[i + 1]); i += 2
    else:
        pos.append(args[i]); i += 1
if len(pos) != 3:
    sys.exit(__doc__)
fx, jp, media = pos
THR, SHORT = opts["thr"], opts["short"]
HOP = 0.05

# --- RMS profile from the media (16 kHz mono s16le via ffmpeg) ---
raw = subprocess.run(
    ["ffmpeg", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar", "16000",
     "-f", "s16le", "-"], capture_output=True, check=True).stdout
x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
n = int(16000 * HOP)
m = len(x) // n
rms = np.sqrt((x[:m * n].reshape(m, n) ** 2).mean(1))

# --- clips ---
rows = []
for mm_ in re.finditer(r"<asset-clip\b[^>]*?(?:/>|>.*?</asset-clip>)", open(fx).read(), re.S):
    a = re.search(r'offset="(\d+)/(\d+)s"[^>]*duration="(\d+)/\d+s"[^>]*start="(\d+)/\d+s"', mm_.group(0))
    f = int(a[2])
    rows.append((int(a[1]) / f, int(a[3]) / f, int(a[4]) / f))  # (tl_offset, dur, src_start)

# --- words ---
d = json.load(open(jp))
W_all = [(w["start"], w["end"], w["word"].strip()) for s in d["segments"] for w in s["words"]
         if "start" in w and "end" in w]
W = [(a, b, w) for a, b, w in W_all if 0.02 <= b - a <= 1.2]


def mm(t):
    return f"{int(t // 60)}:{t % 60:05.2f}"


def loud_frames(t0, t1):
    i0, i1 = max(0, int(t0 / HOP)), min(len(rms), int(t1 / HOP))
    return [(k * HOP, rms[k]) for k in range(i0, i1) if rms[k] > THR]


print(f"## short clips (< {SHORT}s)")
for i, (o, dur, s) in enumerate(rows):
    if dur < SHORT:
        ws = [w for a, b, w in W_all if a < s + dur and b > s and b - a >= 0.02]
        tag = "" if ws else "   <-- NO WORDS: noise fragment?"
        print(f"  {i:3d} src {mm(s)}-{mm(s + dur)} ({dur:.2f}s) words: {' '.join(ws)[:70]}{tag}")

print("## sound within 0.5 s outside a clip edge, not explained by a cut word")
for i in range(1, len(rows)):
    a_end = rows[i - 1][2] + rows[i - 1][1]
    b_start = rows[i][2]
    g = b_start - a_end
    if g < 0.01:
        continue  # contiguous (or a reorder) — nothing to scan
    wg = [(a, b, w) for a, b, w in W if a_end - 0.03 < (a + b) / 2 < b_start + 0.03]
    for side, t0, t1, edge, idx in (
        ("after END   ", a_end, min(b_start, a_end + 0.5), a_end, i - 1),
        ("before START", max(a_end, b_start - 0.5), b_start, b_start, i),
    ):
        lf = [(t, v) for t, v in loud_frames(t0, t1)
              if not any(a - 0.12 <= t <= b + 0.12 for a, b, w in wg)]
        if lf:
            print(f"  {side} clip {idx:3d} edge {mm(edge)} gap {g:5.2f}  sound {mm(lf[0][0])}..{mm(lf[-1][0] + HOP)}"
                  f" max {int(max(v for t, v in lf) * 1000)}  cutwords={' '.join(w for a, b, w in wg)[:50]!r}")
print("## done")
