#!/usr/bin/env python3
"""PaperCut two-pass witness diff: MLX (canonical) SRT vs WhisperX (witness) SRT.
Word-level difflib diff, with MLX block timestamps as context for each disagreement.
Spelled-out numbers are folded to digits on both sides first (MLX says "seventy two",
WhisperX writes "72"), so the list shows real disagreements, not spelling.
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

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 10 ** 6, "billion": 10 ** 9}
_ORD = {w: i for i, w in enumerate(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
    "twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
    "nineteenth".split())}
_ORD.update({w: 10 * (i + 2) for i, w in enumerate(
    "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth".split())})
_ORD.update({"hundredth": 100, "thousandth": 1000})


def _suffix(n):
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def normalize_numbers(toks):
    """Fold spelled-out numbers into digits so MLX's 'seventy two' and WhisperX's
    '72' (or 'eleventh' / '11th') compare equal. A run of number words is summed
    the usual way (tens+units, hundred/thousand multipliers, 'nineteen ninety
    four' as a year); an ordinal word ends the run and takes the suffix. Two
    units in a row ('point zero eight', 'six five') are separate numbers. Digit
    tokens pass through unchanged."""
    out, i = [], 0
    while i < len(toks):
        w = toks[i]
        if w not in _UNITS and w not in _TENS and w not in _ORD and w not in _SCALES:
            out.append(w); i += 1; continue
        total, cur, ordinal, last = 0, 0, None, None
        j = i
        while j < len(toks):
            t = toks[j]
            if t in _UNITS and _UNITS[t] < 10:
                if last not in (None, "tens", "scale"): break
                cur += _UNITS[t]; last = "unit"
            elif t in _UNITS:                                   # ten..nineteen
                if last not in (None, "scale"): break
                cur += _UNITS[t]; last = "teen"
            elif t in _TENS:
                if last in ("teen", "tens") and cur < 100:      # nineteen ninety, twenty twenty
                    cur = cur * 100 + _TENS[t]
                elif last in (None, "scale"):
                    cur += _TENS[t]
                else:
                    break
                last = "tens"
            elif t in _SCALES:
                if last is None and j > i: break
                cur = max(cur, 1) * _SCALES[t]
                if _SCALES[t] >= 1000:
                    total += cur; cur = 0
                last = "scale"
            elif t in _ORD and last == "tens" and _ORD[t] < 10:
                cur += _ORD[t]; ordinal = cur + total; j += 1; break   # twenty first
            elif t in _ORD and j == i:
                ordinal = _ORD[t]; j += 1; break                         # eleventh
            elif t == "and" and last == "scale" and j + 1 < len(toks) and (
                    toks[j + 1] in _UNITS or toks[j + 1] in _TENS):
                pass                                                     # one hundred AND five
            else:
                break
            j += 1
        if ordinal is not None:
            out.append(f"{ordinal}{_suffix(ordinal)}")
        else:
            out.append(str(total + cur))
        i = j
    return out


def tokens(blocks):
    """-> (norm_tokens, timestamp per token). Digit groups keep their commas
    stripped ('1,000' -> '1000'); spelled numbers are folded to digits."""
    toks, ts = [], []
    for t0, text in blocks:
        text = re.sub(r"(?<=\d),(?=\d{3})", "", text.lower())
        ws = re.findall(r"[a-z0-9]+", text)
        ws = normalize_numbers(ws)
        for w in ws:
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
