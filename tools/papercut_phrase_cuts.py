#!/usr/bin/env python3
"""Phrase-anchored FCPXML builder for re-cuts of existing videos (PaperCut companion).

Each cut is a list of segments: (label, source_key, start_phrase, end_phrase, search_after_sec, drops[, hold_end]).
Segments are located by their words in a WhisperX word list (tight word spans), paced with
bridge/margin on the word-span mask, and margins never extend into a word that is not kept.
Use when amplitude silence detection is unusable (continuous speech / high noise floor).

Usage: papercut_phrase_cuts.py <cuts.py> [cut names…]
  cuts.py defines SRC = {key: (media_path, whisperx_json_path)}, OUT = dir, and CUTS = {name: [segments]}.
"""
import sys, json, re, os, numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))  # repo root: silence.py
import silence
from timeline_export import get_media_info, _fcp_format_name, xml_escape

BRIDGE, MARGIN = 0.12, 0.09
norm = lambda w: re.sub(r"[^a-z0-9%]", "", w.lower())
Wd, INFO = {}, {}

def load(k):
    if k in Wd: return
    media, wx = SRC[k]
    d = json.load(open(wx)); segs = d.get("segments", d)
    W = [{"w": w["word"], "t0": w["start"], "t1": w["end"]} for s in segs for w in s.get("words", []) if "start" in w and "end" in w]
    for a, b in zip(W, W[1:]):               # WhisperX sometimes gives a word a ~0.02 s span
        if a["t1"] - a["t0"] < 0.08 and b["t0"] - 0.01 > a["t0"]:
            a["t1"] = min(a["t0"] + 0.18, b["t0"] - 0.01)
    Wd[k] = W; INFO[k] = get_media_info(media)

def fps(k): return INFO[k]["frame_rate_num"] / INFO[k]["frame_rate_den"]

def find(k, phrase, after=0.0):
    W = Wd[k]; p = [norm(x) for x in phrase.split()]
    for i in range(len(W)):
        if W[i]["t0"] >= after and [norm(w["w"]) for w in W[i:i + len(p)]] == p: return i, i + len(p) - 1
    raise SystemExit(f"phrase not found in {k}: {phrase!r} (after {after})")

def seg(k, start, end, after=0.0, drops=(), hold_end=0.0):
    load(k); W = Wd[k]; F = fps(k); n = int(INFO[k]["duration"] * F) + 2
    i, _ = find(k, start, after); _, j = find(k, end, W[i]["t0"])
    keep = set(range(i, j + 1)); cur = W[i]["t0"] - 0.2
    for d in drops:                           # drops resolve in order
        di, dj = find(k, d, cur); keep -= set(range(di, dj + 1)); cur = W[dj]["t1"]
    K = np.zeros(n, bool); O = np.zeros(n, bool)
    for idx, w in enumerate(W):
        a, b = int(w["t0"] * F), int(round(w["t1"] * F)) + 1
        (K if idx in keep else O)[a:b] = True
    O &= ~K                                   # kept words win where spans abut/overlap
    m = K.copy(); silence.bridge_gaps(m, int(round(BRIDGE * F)))
    o = silence.apply_margin(m, int(round(MARGIN * F))); m = o if o is not None else m
    m &= ~O
    if hold_end:                              # real room tone after the last kept word
        e = int(round(W[j]["t1"] * F)); nxt = int(W[j + 1]["t0"] * F) if j + 1 < len(W) else n - 1
        m[e:min(e + int(round(hold_end * F)), nxt - 1)] = True
    return [(k, x, y) for x, y in silence.get_kept_ranges(m, F)]

def build(name, steps):
    items, markers = [], []
    for st in steps:
        if st[0] == "mark": markers.append((len(items), st[1])); continue
        label, k, start, end, after, drops = st[:6]; hold = st[6] if len(st) > 6 else 0.0
        clips = seg(k, start, end, after, drops, hold); markers.append((len(items), label)); items += clips
    used = list(dict.fromkeys(c[0] for c in items)); k0 = used[0]
    N, Dn = INFO[k0]["frame_rate_num"], INFO[k0]["frame_rate_den"]; F = N / Dn
    w, h = INFO[k0]["width"], INFO[k0]["height"]; fr = lambda t: int(round(t * F)); rat = lambda f: f"{f * Dn}/{N}s"
    aid = {k: f"r{n}" for n, k in enumerate(used, 2)}
    R = [f'    <format id="r1" name="{_fcp_format_name(w, h, N, Dn)}" frameDuration="{Dn}/{N}s" width="{w}" height="{h}" colorSpace="1-1-1 (Rec. 709)"/>']
    for k in used:
        p = SRC[k][0]; stem = xml_escape(os.path.basename(p).rsplit(".", 1)[0])
        R.append(f'    <asset id="{aid[k]}" name="{stem}" start="0s" hasVideo="1" format="r1" hasAudio="1" audioSources="1" audioChannels="2" duration="{rat(fr(INFO[k]["duration"]))}">\n      <media-rep kind="original-media" src="file://{xml_escape(p)}"/>\n    </asset>')
    mk = dict(markers); spine = []; tl = 0
    for idx, (k, a, b) in enumerate(items):
        fi, fo = fr(a), fr(b); d = fo - fi
        if d <= 0: continue
        stem = xml_escape(os.path.basename(SRC[k][0]).rsplit(".", 1)[0])
        attrs = f'name="{stem}" ref="{aid[k]}" offset="{rat(tl)}" duration="{rat(d)}" start="{rat(fi)}" tcFormat="NDF"'
        spine.append(f'          <asset-clip {attrs}>\n            <marker start="{rat(fi)}" duration="{rat(1)}" value="{xml_escape(mk[idx])}"/>\n          </asset-clip>' if idx in mk else f'          <asset-clip {attrs}/>')
        tl += d
    x = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.11">\n  <resources>\n' + "\n".join(R) +
         f'\n  </resources>\n  <library>\n    <event name="PaperCut Import">\n      <project name="{name}">\n        <sequence format="r1" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">\n          <spine>\n'
         + "\n".join(spine) + '\n          </spine>\n        </sequence>\n      </project>\n    </event>\n  </library>\n</fcpxml>\n')
    open(os.path.join(OUT, name + ".fcpxml"), "w").write(x)
    text = " ".join(w["w"] for k, a, b in items for w in Wd[k] if a - 0.02 <= w["t0"] and w["t1"] <= b + 0.02)
    secs = tl / F; print(f"{name}: {len(items)} clips, {int(secs // 60)}:{secs % 60:04.1f}")
    return text

if __name__ == "__main__":
    ns = {}; exec(open(sys.argv[1]).read(), ns); SRC, OUT, CUTS = ns["SRC"], ns["OUT"], ns["CUTS"]
    for n in (sys.argv[2:] or CUTS):
        t = build(n, CUTS[n]); open(os.path.join(ns.get("TEXT_OUT", OUT), f"{n}.played.txt"), "w").write(t)
