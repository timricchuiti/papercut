#!/usr/bin/env python3
"""papercut_words.py <whisper.json> <lo-hi> [<lo-hi> ...]
Dump word timings (seconds) in each source range; flags <ZERO> (zero-width ghost words from an
MLX decoding loop) and <LONG> (>1.5 s, usually a sentence-final word stretched over the pause)."""
import json,sys
d=json.load(open(sys.argv[1]))
W=[]
segs=d.get('segments',d if isinstance(d,list) else [])
for s in segs:
    for w in s.get('words',[]):
        W.append((w['start'],w['end'],w['word'].strip()))
def mm(t): return f"{int(t//60)}:{t%60:05.2f}"
for rng in sys.argv[2:]:
    lo,hi=[float(x) for x in rng.split('-')]
    print(f"== {mm(lo)}-{mm(hi)}")
    for s,e,w in W:
        if e>=lo and s<=hi:
            flag=' <ZERO>' if e-s<0.01 else (' <LONG>' if e-s>1.5 else '')
            print(f"  {mm(s)}-{mm(e)} {w}{flag}")
