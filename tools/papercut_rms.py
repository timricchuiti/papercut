#!/usr/bin/env python
"""RMS loudness profile of a media file, and the loud runs inside chosen ranges.

  papercut_rms.py <media|wav> <out.npy>                 build the 50 ms RMS profile
  papercut_rms.py <out.npy> [--thr 0.008] <lo-hi> ...   print loud runs (seconds) in each range

The .npy is one float per 50 ms bin (20 bins/s). Used to decide whether a word MLX
timed as a ghost is really in the audio, where a pause actually starts, etc.
"""
import sys, subprocess, numpy as np

BIN = 0.05

def build(media, out):
    cmd = ["ffmpeg", "-v", "error", "-i", media, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.float32)
    n = int(BIN * 16000)
    nb = len(x) // n
    r = np.sqrt((x[: nb * n].reshape(nb, n) ** 2).mean(axis=1))
    np.save(out, r)
    print(f"{nb} bins {nb * BIN:.1f} s; median {np.median(r):.4f} -> {out}")

def runs(npy, thr, ranges):
    r = np.load(npy)
    for rng in ranges:
        lo, hi = map(float, rng.split("-"))
        a, b = int(lo / BIN), int(hi / BIN)
        on = r[a:b] > thr
        print(f"== {rng}")
        i = 0
        while i < len(on):
            if on[i]:
                j = i
                while j < len(on) and on[j]:
                    j += 1
                print(f"  loud {lo + i * BIN:.2f}-{lo + j * BIN:.2f} peak {r[a + i:a + j].max():.3f}")
                i = j
            else:
                i += 1

if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2 and not args[0].endswith(".npy"):
        build(*args)
    else:
        thr = 0.008
        if "--thr" in args:
            k = args.index("--thr"); thr = float(args[k + 1]); del args[k:k + 2]
        runs(args[0], thr, args[1:])
