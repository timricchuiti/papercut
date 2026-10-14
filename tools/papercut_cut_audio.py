#!/usr/bin/env python3
"""papercut_cut_audio.py — reconstruct the CUT audio from a papercut _ALTERED.fcpxml.

The fcpxml encodes the keep-segments as <asset-clip> elements:
  offset   = position in the RESULT timeline
  start    = position in the SOURCE (the PC.mp4)
  duration = segment length
all as rational "N/Ds" (or "Ns") frame values. We extract each source segment from
the PC.mp4 and concat in RESULT order -> the cut audio == what the finished edit sounds
like. Transcribing THIS gives result-time cues directly (no source->result mapping).

Usage:
  papercut_cut_audio.py <ALTERED.fcpxml> <PC.mp4> <out.m4a>

This is the deterministic half of the P3b rough_animate runner (docs/pipeline-app-plan).
"""
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def _rat(s):
    """'205/60s' | '3s' | '0s' -> float seconds."""
    s = s.strip().rstrip("s")
    if "/" in s:
        n, d = s.split("/")
        return float(n) / float(d)
    return float(s)


def keep_segments(fcpxml):
    """[(offset, start, duration)] in seconds, in result (offset) order."""
    root = ET.parse(fcpxml).getroot()
    segs = []
    for clip in root.iter("asset-clip"):
        segs.append((_rat(clip.get("offset", "0s")),
                     _rat(clip.get("start", "0s")),
                     _rat(clip.get("duration", "0s"))))
    # also handle plain <clip>/<ref-clip> spine items if present
    if not segs:
        for clip in root.iter("clip"):
            segs.append((_rat(clip.get("offset", "0s")),
                         _rat(clip.get("start", "0s")),
                         _rat(clip.get("duration", "0s"))))
    return sorted(segs, key=lambda s: s[0])


def build(fcpxml, pc_mp4, out):
    segs = keep_segments(fcpxml)
    if not segs:
        sys.exit("no keep-segments found in fcpxml")
    total = sum(d for _, _, d in segs)
    print(f"{len(segs)} keep-segments, result length {total:.2f}s")
    with tempfile.TemporaryDirectory() as td:
        parts = []
        for i, (off, start, dur) in enumerate(segs):
            p = Path(td) / f"seg_{i:03d}.m4a"
            subprocess.run(["/opt/homebrew/bin/ffmpeg", "-y", "-ss", f"{start:.4f}",
                            "-i", str(pc_mp4), "-t", f"{dur:.4f}", "-vn",
                            "-c:a", "aac", "-b:a", "192k", str(p)],
                           check=True, capture_output=True)
            parts.append(p)
        listf = Path(td) / "list.txt"
        listf.write_text("".join(f"file '{p}'\n" for p in parts))
        subprocess.run(["/opt/homebrew/bin/ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", str(listf), "-c", "copy", str(out)],
                       check=True, capture_output=True)
    d = subprocess.run(["/opt/homebrew/bin/ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout.strip()
    print(f"wrote {out}  ({d}s)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    build(sys.argv[1], sys.argv[2], sys.argv[3])
