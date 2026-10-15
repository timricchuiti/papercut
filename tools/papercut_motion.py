#!/usr/bin/env python3
"""papercut_motion.py <media> <out_cnt10.npy> [--thr 24]

Screen-motion profile for papercut_pausepolicy.py --motion: decode the media to a
320x180 grayscale proxy at 10 fps (ffmpeg, rawvideo) and store, per 0.1 s frame,
the number of pixels whose value changed by more than --thr since the previous
frame. Still screen -> 0; typing / dropdowns / page scrolls -> tens to thousands.
"""
import sys, subprocess, numpy as np
args = sys.argv[1:]; thr = 24
if '--thr' in args:
    i = args.index('--thr'); thr = int(args[i + 1]); del args[i:i + 2]
media, out = args
W, H = 320, 180
p = subprocess.run(['ffmpeg', '-v', 'error', '-i', media, '-vf', f'fps=10,scale={W}:{H},format=gray',
                    '-f', 'rawvideo', '-pix_fmt', 'gray', '-'], stdout=subprocess.PIPE, check=True)
buf = np.frombuffer(p.stdout, dtype=np.uint8)
n = len(buf) // (W * H)
frames = buf[:n * W * H].reshape(n, H, W).astype(np.int16)
cnt = np.zeros(n, dtype=np.int32)
cnt[1:] = (np.abs(frames[1:] - frames[:-1]) > thr).reshape(n - 1, -1).sum(axis=1)
np.save(out, cnt)
moving = int((cnt > 2).sum())
print(f"{n} frames ({n/10:.1f} s), {moving} with motion ({moving/max(n,1)*100:.1f} %), max {cnt.max()} px -> {out}")
