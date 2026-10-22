# tools/ — post-export and verification helpers

Small single-purpose scripts that work on PaperCut outputs (FCPXML, word JSON,
SRTs). All run under `.venv-crisper/bin/python` (they need numpy/soundfile; the
FCPXML ones are pure stdlib). Each takes the FCPXML in place and **re-tiles
timeline offsets** so the spine still tiles; markers travel with their clips.
`~/Documents/Projects/_claude/papercut_*.py` are symlinks to these files.

| Tool | Does |
|---|---|
| `papercut_witness_diff.py <mlx.srt> <wx.srt>` | Word-level diff of the MLX transcript against the WhisperX witness, with MLX timestamps as context. Spelled-out numbers are folded to digits on both sides first ("seventy two" = "72", "eleventh" = "11th"), so the list is real disagreements. Step 2 of the two-pass workflow. |
| `papercut_uncovered.py <json> <media>` | Pre-edit scan: runs of audio the transcript's words don't cover (a phrase MLX never emitted or timed into a silence, or non-speech like a cough/taps). Check each against the witness + waveform before editing. Computes RMS from the media. |
| `papercut_words.py <json> <lo-hi> …` | Dump word timings in source ranges; flags `<ZERO>` ghost words and `<LONG>` pause-stretched words. |
| `papercut_clips.py <fcpxml> [lo hi]` | List clips (timeline pos, source in/out, gap, markers); last line = count, tiles?, total, markers. |
| `papercut_edgescan.py <fcpxml> <json> <media>` | Post-export sanity scan: short no-word clips (noise fragments) and sound just outside a clip edge that no cut word explains (clipped tail/onset). Computes RMS from the media itself. |
| `papercut_setin.py <fcpxml> <approx_src_start> <new>` | Move one clip's in-point either way: earlier (recover a clipped onset) or later (keep a cut word's tail out of the next clip). |
| `papercut_setout.py <fcpxml> <approx_src_end> <new>` | Extend/trim one clip's out-point (recover a clipped tail). |
| `papercut_dropclips.py <fcpxml> <lo> <hi>` | Drop every clip whose SOURCE start is in [lo, hi] s (noise fragments). **Seconds, not mm:ss** — 19:15 is 1155. |
| `papercut_addclip.py <fcpxml> <src_start> <src_end>` | Insert a clip for speech the export left out entirely (the transcript had no block there, e.g. MLX timestamped a line into a silence); placed in source order, offsets re-tiled. Use setin/setout when the missing speech is adjacent to a clip. |
| `papercut_moveclips.py <fcpxml> <lo> <hi> <after>` | Move a group of clips to sit after another source time (structural reorder). |
| `papercut_breaths.py <video> <fcpxml> <extra_s> <t…>` | Widen the pause at chosen boundaries using the source's own room tone. |
| `papercut_motion.py <media> <cnt10.npy>` | Screen-motion profile (changed-pixel count per 0.1 s from a 320×180 gray ffmpeg proxy) — input to the pause policy; handy for finding where a click lands in a pause. |
| `papercut_pausepolicy.py <fcpxml> <json> <audio16k.wav> [--motion cnt10.npy] [--keep 2] [--air 1] [--override t=air]` | Experimental pause policy (pure pauses ≤ keep stay whole, longer ones cut to `air` seconds placed by screen motion). **Withdrawn 2026-10-14** — Tim wants DM lessons tight except between problems; see EDITING_PROTOCOL.md. Kept for reference. |
| `papercut_cut_audio.py <fcpxml> <media> <out.m4a>` | Render the cut's audio (concat of the kept spans) — result-time cues for animation timing. |
| `papercut_phrase_cuts.py <cuts.py> [names…]` | Phrase-anchored re-cuts of existing videos from a word list, for sources where amplitude silence detection is unusable. |

Mouse-click slivers (isolated 0.1–0.2 s no-word clips in long pauses) are dropped by
the exporter itself now (`--sliver`, default 0.25 s; the export message counts them),
so `papercut_dropclips.py` is for the rest: a word MLX timed onto a cough, a burst
of taps the transcript covers.

Typical post-export sequence for a cut:

```bash
P=.venv-crisper/bin/python
$P tools/papercut_edgescan.py X_ALTERED.fcpxml X.json X.mp4   # look at every line it prints
$P tools/papercut_setout.py  X_ALTERED.fcpxml 815.9 816.75    # a clipped tail the scan found
$P tools/papercut_dropclips.py X_ALTERED.fcpxml 1155.0 1155.1 # a no-word fragment
$P tools/papercut_clips.py   X_ALTERED.fcpxml | tail -1        # tiles=True, totals
```
