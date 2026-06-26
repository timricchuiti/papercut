# CLAUDE.md — PaperCut

## Project Overview

A transcript-based editing tool for video and audio. Transcribe media via CrisperWhisper (verbatim — keeps fillers + repeats) or WhisperX, then cut/edit, and export as FCPXML (Final Cut Pro / DaVinci Resolve), Premiere XML, or re-encoded media via ffmpeg.

**Two interfaces, one export core (`papercut_core.py`):**
- **Batch / headless (`batch.py`)** — primary for Claude-driven editing of many files. Transcribe a directory, Claude edits the SRTs, export FCP XMLs next to each source. See `EDITING_PROTOCOL.md`.
- **Browser GUI (`python3 web_gui.py`)** — two-pane interactive editor at localhost:5000.

## Architecture

```
Media file → WhisperX/CrisperWhisper → .json + .srt + .srt.orig
     ↓
Browser UI: two-pane editor (original | edit)
  - Cut, reorder (drag-and-drop), text edit blocks
  - Free Edit mode: edit as raw SRT text
     ↓
On export:
  1. silence.py — amplitude-based silence detection (numpy + ffmpeg)
  2. timeline_export.py — build ordered clip list, generate output
  3. Output: FCPXML / Premiere XML / video via ffmpeg concat
```

## Key Files

| File | Purpose |
|---|---|
| `batch.py` | Headless batch driver — `transcribe` / `export` / `status` over a directory |
| `papercut_core.py` | Shared export core — `export_from_srt()`, `export_from_blocks()`, `resolve_word_edits()`; used by both GUI and batch |
| `EDITING_PROTOCOL.md` | How Claude edits SRTs (keep-last-of-repeats, flagging, sense-check) |
| `web_gui.py` | Flask backend — upload, transcribe (SSE), diff, export (delegates to `papercut_core`) |
| `static/index.html` | Full single-page editor UI |
| `static/landing.html` | Marketing landing page |
| `silence.py` | Silence detection: `detect_silence()`, `apply_margin()`, `get_kept_ranges()` |
| `timeline_export.py` | `Clip` dataclass, `build_clip_list()`, `generate_fcpxml()`, `generate_premiere_xml()`, `export_video()` |
| `transcript_diff.py` | SRT parser, WhisperX JSON loader, deleted range detection |
| `auto_transcript.py` | WhisperX/CrisperWhisper wrapper for transcription |
| `main.py` | Single-file CLI (transcribe / edit / export via `papercut_core`) |

## Tech Stack

- FFmpeg required (`brew install ffmpeg`). No auto-editor dependency.
- **CrisperWhisper venv (`.venv-crisper/`, gitignored)** — verbatim engine. Run batch.py/main.py with `.venv-crisper/bin/python`. Recipe:
  ```bash
  /opt/homebrew/bin/python3.12 -m venv .venv-crisper
  .venv-crisper/bin/python -m pip install torch torchaudio accelerate safetensors librosa soundfile numpy
  .venv-crisper/bin/python -m pip install "git+https://github.com/nyrahealth/transformers.git@crisper_whisper"
  ```
  The nyrahealth transformers fork (v4.37.2) is REQUIRED — stock transformers' word-timestamp extraction is incompatible with CrisperWhisper. `auto_transcript.py` auto-applies a `_postprocess_outputs` patch only when the fork is detected (guarded by signature inspection).
- WhisperX (optional engine) lives in its own pipx venv (Python 3.12).

## Running

```bash
# Batch (headless) — primary for Claude. Run under the venv:
.venv-crisper/bin/python batch.py transcribe <dir>   # verbatim transcripts
.venv-crisper/bin/python batch.py status <dir>       # show pipeline state
.venv-crisper/bin/python batch.py export <dir>       # FCP XMLs next to sources

# Single file:
.venv-crisper/bin/python main.py video.mp4 --transcribe-only
.venv-crisper/bin/python main.py video.mp4 --export final-cut-pro

# Browser GUI:
python3 web_gui.py --port 5009
```

## Export Details

- FCPXML imports into FCP as event "PaperCut Import" with project `{filename}_ALTERED`
- Silence detection threshold parsed from `edit_method` field (e.g. `audio:threshold=0.04`)
- Export pipeline: ordered_blocks from frontend → silence detection → clip list → output format

## Design Constraints

- **JSON is source of truth** for word-level timestamps — SRT timestamps are approximate
- **Block-level operations** in block mode; free-form editing in Free Edit mode
- **Non-monotonic/corrupt SRT timestamps** → log warning, skip block, don't crash
- FCPXML exports reference media by absolute file path; original file must stay in place
- Supports both video and audio-only files across all export formats

## Development Guidelines

- **Do NOT commit or push unless explicitly asked.** Wait for Tim to say when he wants a commit.
- Keep modules independent and testable — each file should work as a standalone unit
- SRT parsing must be robust against messy edits (extra blank lines, missing block numbers, etc.)
- Write to stdout for status messages, stderr for warnings/errors

## Frontend Features

- Two-pane view: original (read-only diff display) | edit (interactive)
- Block mode: cut/restore, drag-and-drop reorder, inline text editing
- Free Edit mode: toggle to edit the entire transcript as raw SRT text
- Undo/redo stack (supports cut, restore, edit, reorder actions)
- Search/find across transcript blocks
- Auto-edit tools: Clean Fillers, Dedupe Takes
- Auto-save to localStorage (preserves cuts, edits, and block order)
- Export presets saved to localStorage
- Video/audio sync playback with scroll sync
- Dark mode toggle
- Keyboard shortcuts: Ctrl+Z/Y undo/redo, Ctrl+F search, arrow keys nav, Delete to cut
