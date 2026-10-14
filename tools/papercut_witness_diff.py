#!/usr/bin/env python3
"""PaperCut two-pass witness diff: MLX (canonical) SRT vs WhisperX (witness) SRT.
Word-level difflib diff, with MLX block timestamps as context for each disagreement.
Usage: papercut_witness_diff.py <mlx.srt> <whisperx.srt>"""
import re, sys, difflib

def parse_srt(path):
    """-> list of (start_str, text) blocks."""
    blocks, cur_ts, cur = [], None, []
    for line in open(path, encoding="utf-8", errors="replace"):
        s = line.strip()
        if not s:
            if cur_ts and cur: blocks.append((cur_ts, " ".join(cur)))
            cur_ts, cur = None, []
        elif "-->" in s:
            cur_ts = s.split("-->")[0].strip()[:8]
        elif s.isdigit() and cur_ts is None:
            continue
        else:
            cur.append(s)
    if cur_ts and cur: blocks.append((cur_ts, " ".join(cur)))
    return blocks

def tokens(blocks):
    """-> (norm_tokens, timestamp per token)"""
    toks, ts = [], []
    for t0, text in blocks:
        for w in re.findall(r"[a-z0-9]+", text.lower()):
            toks.append(w); ts.append(t0)
    return toks, ts

mlx, wx = parse_srt(sys.argv[1]), parse_srt(sys.argv[2])
a, ta = tokens(mlx); b, tb = tokens(wx)
sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
print(f"similarity {sm.ratio()*100:.1f}%  MLX words {len(a)}  WX words {len(b)}  disagreements {len(ops)}\n")
for tag, i1, i2, j1, j2 in ops:
    ctx = " ".join(a[max(0, i1-5):i1])
    t = ta[i1] if i1 < len(ta) else ta[-1]
    print(f"[{tag:7}] @{t}  …{ctx}|")
    print(f"   MLX: {' '.join(a[i1:i2])!r}")
    print(f"   WX : {' '.join(b[j1:j2])!r}")
