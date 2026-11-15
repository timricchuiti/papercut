#!/usr/bin/env python3
"""papercut_cut_av.py — render the CUT as a small AV file from a papercut _ALTERED.fcpxml.

Same idea as papercut_cut_audio.py (keep-segments from the fcpxml's <asset-clip> offset/start/
duration, extracted from the source and concatenated in result order) but with picture, so the
result is the "AVfortiming" export the painted pipeline times its animation to (the audio is the
cut VO; the picture is whatever the PC recording showed). Default 720x1280 @ 60 fps, h264 + aac,
matching Tim's FCP AVfortiming exports.

Usage:
  papercut_cut_av.py <ALTERED.fcpxml> <PC.mp4> <out.mp4> [--size 720x1280] [--fps 60]
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from papercut_cut_audio import keep_segments  # noqa: E402

FF = "/opt/homebrew/bin/ffmpeg"


def build(fcpxml, src, out, size="720x1280", fps=60):
    segs = keep_segments(fcpxml)
    if not segs:
        sys.exit("no keep-segments found in fcpxml")
    w, h = size.split("x")
    total = sum(d for _, _, d in segs)
    print(f"{len(segs)} keep-segments, result length {total:.2f}s")
    with tempfile.TemporaryDirectory() as td:
        parts = []
        for i, (off, start, dur) in enumerate(segs):
            p = Path(td) / f"seg_{i:03d}.mp4"
            subprocess.run([FF, "-y", "-ss", f"{start:.4f}", "-i", str(src), "-t", f"{dur:.4f}",
                            "-map", "0:v:0", "-map", "0:a:0",
                            "-vf", f"scale={w}:{h},fps={fps}",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                            "-video_track_timescale", "60000", str(p)],
                           check=True, capture_output=True)
            parts.append(p)
        listf = Path(td) / "list.txt"
        listf.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run([FF, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
                        "-c", "copy", "-movflags", "+faststart", str(out)],
                       check=True, capture_output=True)
    d = subprocess.run(["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout.strip()
    print(f"wrote {out}  ({d}s)")


if __name__ == "__main__":
    a = sys.argv[1:]
    size, fps = "720x1280", 60
    if "--size" in a:
        k = a.index("--size"); size = a[k + 1]; del a[k:k + 2]
    if "--fps" in a:
        k = a.index("--fps"); fps = int(a[k + 1]); del a[k:k + 2]
    if len(a) != 3:
        sys.exit(__doc__)
    build(a[0], a[1], a[2], size, fps)
