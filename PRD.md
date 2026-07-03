# Product Requirements Document: PaperCut

**Project Title:** PaperCut
**Purpose:** Transcript-based video/audio editing. Transcribe media *verbatim*
(fillers, stutters, and false starts preserved), edit the transcript as text —
delete blocks, cut words, reorder — and export the result as an NLE timeline
(FCPXML / Premiere XML) or a re-encoded file. Silence is removed automatically;
the transcript edits and the silence cuts compose into one clip list.

**Status:** v2 — this document describes the tool as it exists. The original v1
PRD (an `auto-editor` wrapper around WhisperX, deletion-only) is fully superseded:
auto-editor is no longer a dependency (silence detection, clip building, and all
exporters are implemented in-repo), and the default engine is CrisperWhisper
running on Apple MLX.

---

## 1. 🧭 Strategic Goals

* **Verbatim first.** The transcript must show what was actually said — flubs,
  repeats, "um"s — because those are exactly the things being edited out.
* **Text is the edit surface.** Deleting/marking text in an `.srt` (or the browser
  GUI) is the entire editing model. No timeline scrubbing required before the NLE.
* **Non-destructive NLE handoff.** Exports are cut lists referencing the original
  media; every cut can be re-expanded in Final Cut Pro afterward.
* **Batch/headless as a first-class mode.** Directories of lecture recordings are
  processed by an LLM operator (Claude) end-to-end: transcribe → edit → flag →
  export (see `EDITING_PROTOCOL.md`).
* **Fast on Apple Silicon.** A 9-minute recording transcribes in ~30–40 s.

---

## 2. 🧩 Key Features (implemented)

### 2.1. Transcription — three engines, one dispatcher (`auto_transcript.py`)
| Engine | What it is | When |
| --- | --- | --- |
| **`mlx`** (default) | CrisperWhisper's verbatim weights on Apple MLX (`mlx_transcribe.py`, own venv `.venv-mlx`, built by `./setup_mlx.sh`). Restores CrisperWhisper's alignment heads (accurate DTW word timestamps) and standalone-space word grouping (whole words). ~15–30× faster than the transformers path. | Always, on Apple Silicon |
| `crisperwhisper` | The same model via the nyrahealth transformers fork, in-process (`.venv-crisper`). | Fallback / non-MLX |
| `whisperx` | WhisperX CLI (own pipx venv). Non-verbatim; has diarization/word-align extras. | Fallback for files that crash CrisperWhisper's timestamp step |

**Outputs per file:** `<stem>.json` (word-level timestamps — the timing source of
truth), `<stem>.srt` (editable), `<stem>.srt.orig` (pristine diff baseline).

### 2.2. Editing model
* **Block deletion** — remove an SRT block to cut that span.
* **Word-level cuts** — `[[CUT]]…[[/CUT]]` around exact words inside a block.
  Placement is positional (token ↔ word 1:1); if the tokens don't line up, the
  block is kept WHOLE and a warning is printed — never a fuzzy wrong cut.
* **Keep-last resolution** — free-text edits match RIGHTMOST, so trimming a
  repeated take keeps the final delivery.
* **Reordering** — blocks are exported in file order (GUI: drag-and-drop).
* **`[[FLAG: note]]`** — becomes a named FCPXML `<marker>` on the containing clip,
  so review points show up on the FCP timeline. Stripped before word matching.
* **Timestamps in the SRT are advisory** — timing always comes from the JSON.

### 2.3. Silence handling — two independent knobs
* **`--bridge`** (default **0.20 s**): silent gaps up to ~2× this inside speech
  are kept (natural micro-pauses; prevents a sentence shattering into dozens of
  clips). Longer pauses are cut. Implemented as a morphological close.
* **`--margin`** (default **0.07 s**): edge tightness only. `>0` pads breath
  around each clip, `0` sits at the detected speech, `<0` erodes into it.
  Applied exactly once (`silence.apply_margin`); `build_clip_list` adds none.
* Detection is amplitude-based per video frame (`silence.py`, numpy + ffmpeg),
  threshold 0.04 (overridable, incl. via `edit_method` strings).

### 2.4. Clip building & export (`timeline_export.py`, `papercut_core.py`)
* Kept blocks ∩ loud ranges → spans; contiguous spans merge; residual source
  overlaps clamp at the midpoint (**no replayed audio at seams**).
* Frame-exact tiling: in/out points round to frames, duration = frame difference;
  timeline offsets accumulate in integer frames (**no 1–2 frame gap clips**).
* Sliver filter drops clips < 0.1 s.
* **Formats:** FCPXML 1.11 (Final Cut Pro & DaVinci Resolve — event "PaperCut
  Import", project `<stem>_ALTERED`), Premiere XML, or re-encoded media via
  ffmpeg concat. Video and audio-only sources both supported.
* **Guardrail:** every FCPXML is validated post-write (tiny clips, tiling
  mismatches) and warnings surface in the report.

### 2.5. Interfaces
* **`batch.py`** — headless over a directory: `transcribe` / `status` / `export`.
  Primary interface for the Claude-driven workflow (`EDITING_PROTOCOL.md`).
* **`main.py`** — single file: `--transcribe-only` / `--edit-transcript` /
  `--export … [--margin] [--bridge] [--threshold] [--output]`.
* **`web_gui.py`** — Flask + `static/index.html`: two-pane editor (original diff |
  edit), block cut/restore/reorder, Free Edit mode, undo/redo, search, auto-edit
  tools (Clean Fillers, Dedupe Takes), engine picker (mlx default), SSE progress,
  localStorage persistence, dark mode.

---

## 3. ⚙️ Architecture

```
Media ──► auto_transcript.py ──► <stem>.json + .srt + .srt.orig
              │ (engine dispatch: mlx → .venv-mlx subprocess;
              │  crisperwhisper → in-process; whisperx → CLI)
              ▼
   User/Claude edits .srt  (delete blocks, [[CUT]] words, [[FLAG]] notes)
              ▼
papercut_core.export_from_srt / export_from_blocks
   ├─ srt_to_ordered_blocks()      edited vs .orig, time-aligned
   ├─ extract_flags()              [[FLAG]] → marker list
   ├─ resolve_word_edits()         exact [[CUT]] placement / rightmost match
   ├─ silence.detect_silence()     + bridge_gaps() + apply_margin()
   ├─ build_clip_list()            blocks ∩ loud ranges, overlap-free
   └─ write_export()               FCPXML (+markers) / Premiere / ffmpeg
              ▼
<stem>_ALTERED.fcpxml  (+ validate_fcpxml guardrail)
```

**Key files:** `papercut_core.py` (shared export core), `auto_transcript.py`,
`mlx_transcribe.py`, `silence.py`, `timeline_export.py`, `transcript_diff.py`,
`batch.py`, `main.py`, `web_gui.py` + `static/index.html`, `setup_mlx.sh`.

**Tech:** Python 3.12+, ffmpeg, numpy; MLX (pinned `mlx_whisper` from
mlx-examples — pip 0.4.3 is incompatible); nyrahealth transformers fork for the
crisperwhisper engine. No auto-editor.

---

## 4. 🧰 Canonical workflows

### Batch (Claude-driven — the primary loop)
```bash
.venv-crisper/bin/python batch.py transcribe "<dir>"   # MLX, verbatim
#   … edit each <stem>.srt; write <stem>.notes.md; add [[FLAG]]s …
.venv-crisper/bin/python batch.py export "<dir>"       # FCPXMLs + guardrail
```

### Single file
```bash
.venv-crisper/bin/python main.py video.mp4 --transcribe-only
.venv-crisper/bin/python main.py video.mp4 --export final-cut-pro
# knobs when needed: --margin 0.07 --bridge 0.20 --threshold 0.04
```

### GUI
```bash
python3 web_gui.py --port 5009
```

---

## 5. 📏 Safeguards

* **JSON is the timing truth** — SRT timestamp edits are ignored.
* **Unplaceable `[[CUT]]` ⇒ keep whole + warn** (never guess).
* **Corrupt/non-monotonic SRT blocks** ⇒ warn and skip, don't crash.
* **Post-export validation** — tiny-clip / tiling guardrail on every FCPXML.
* **FCPXML references media by absolute path** — the source file must stay put.
* **Flag-not-fix** (protocol): content/math errors are flagged for Tim, never
  silently corrected.

---

## 6. 🔮 Roadmap (current)

* **MLX warm batch** — keep the MLX model loaded across a multi-file batch
  (today it reloads per file, ~seconds each; `--in-process` only warms the
  crisperwhisper engine).
* **Premiere markers** — `[[FLAG]]` markers currently emit in FCPXML only.
* **`--summary` breakdown** — split "removed by silence" vs "removed by edits"
  in the export report.
* **GUI bridge control** — the bridge knob is API-exposed but has no UI field.
* Done from v1's roadmap: word-level editing, silence snapping, GUI editor,
  reordering, mark mode ([[FLAG]]). Dropped: `--preview` (review happens in FCP),
  rich markup, multicam.

---

## 7. 📜 License
MIT. Engines: [CrisperWhisper](https://github.com/nyrahealth/CrisperWhisper)
(nyrahealth), [Apple MLX](https://github.com/ml-explore/mlx) /
`mlx_whisper`, [WhisperX](https://github.com/m-bain/whisperx) (BSD-2-Clause).
(auto-editor inspired the original design but is no longer used.)

---

## 8. 📊 Success Criteria — v2 (all currently met)

- [x] Verbatim transcription (fillers/stutters preserved) with accurate word
      timestamps (median ~20–60 ms vs reference)
- [x] 9-minute video transcribes in <1 minute on Apple Silicon
- [x] 100% of deleted blocks / placed `[[CUT]]`s applied; unplaceable cuts kept
      whole + warned
- [x] FCPXML imports into FCP with no warnings; zero clip-boundary overlaps;
      zero sub-0.1s slivers (validated automatically per export)
- [x] Silence cut with natural micro-pauses preserved (bridge) and tight,
      configurable edges (margin)
- [x] Zero crashes on malformed SRT edits
