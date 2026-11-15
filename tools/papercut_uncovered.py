#!/usr/bin/env python3
"""papercut_uncovered.py <json> <media> [--thr 0.01] [--min 0.3]
Pre-edit scan: runs of audio (RMS > thr, 16 kHz mono, 50 ms windows, merged across
0.4 s gaps, at least --min long) that the transcript's words cover by less than half.
These are phrases the transcriber never emitted (ghost loops, a line timed into a
silence, a missed sentence) — or non-speech (a cough, a burst of taps). Check each
against the WhisperX witness and the waveform before editing; a real phrase can be
left in a kept block, restored with papercut_setout.py / papercut_addclip.py, and
flagged. Prints: source span, length, covered seconds, nearest transcript words."""
import json, subprocess, argparse
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("json"); ap.add_argument("media")
ap.add_argument("--thr", type=float, default=0.01, help="RMS threshold for 'sound' (default 0.01)")
ap.add_argument("--min", type=float, default=0.3, help="shortest run to report, seconds (default 0.3)")
a = ap.parse_args()
HOP, SR = 0.05, 16000
raw = subprocess.run(["ffmpeg", "-v", "error", "-i", a.media, "-vn", "-ac", "1", "-ar", str(SR),
                      "-f", "s16le", "pipe:1"], capture_output=True, timeout=900, check=True).stdout
x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
n = int(SR * HOP); m = len(x) // n
r = np.sqrt((x[:m * n].reshape(m, n) ** 2).mean(1))

j = json.load(open(a.json))
words = [(w["start"], w["end"], w["word"]) for seg in j.get("segments", []) for w in seg.get("words", [])
         if "start" in w and "end" in w and w["end"] > w["start"]]
loud = r > a.thr; runs = []; i = 0; gap = int(0.4 / HOP)
while i < m:
    if loud[i]:
        s = i
        while i < m and (loud[i] or loud[i:i + gap].any()): i += 1
        runs.append((s * HOP, i * HOP))
    else:
        i += 1
def mm(t): return f"{int(t // 60)}:{t % 60:05.2f}"
found = 0
for s, e in runs:
    if e - s < a.min: continue
    cov = sum(max(0.0, min(e, we) - max(s, ws)) for ws, we, _ in words)
    if cov < (e - s) * 0.5:
        found += 1
        near = " ".join(w for ws, we, w in words if ws < e + 2 and we > s - 2)
        print(f"{mm(s)}-{mm(e)}  len {e - s:4.1f}s  covered {cov:.1f}s  near: {near[:90]}")
print(f"{found} uncovered run(s) >= {a.min}s (threshold {a.thr})")
