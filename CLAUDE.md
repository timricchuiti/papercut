# CLAUDE.md — PaperCut

## Project Overview

A Python CLI tool that wraps [auto-editor](https://github.com/WyattBlue/auto-editor) with a transcript-based editing layer. Users transcribe video via WhisperX, delete unwanted blocks from an `.srt` file, and the tool merges those cuts with auto-editor's silence detection before exporting (FCPXML, Premiere XML, DaVinci Resolve, or re-encoded video).

**Status:** Greenfield — PRD and README exist, no application code written yet.

## Key Documents

- `PRD.md` — Full product requirements, architecture, CLI flags, roadmap
- `README.md` — User-facing docs and usage examples

## Architecture (from PRD)

```
Video → WhisperX → .json (word timestamps) + .srt (editable)
          ↓
   User deletes SRT blocks
          ↓
   Diff engine (original vs edited SRT, timestamps from JSON)
          ↓
   Merge engine (transcript cuts ∪ auto-editor silence cuts)
          ↓
   auto-editor export (FCPXML / Premiere / video)
```

### Planned Files

| File | Purpose |
|---|---|
| `auto_transcript.py` | WhisperX wrapper — generates `.json` + `.srt` from video |
| `transcript_diff.py` | Diffs original vs edited SRT, extracts cut ranges from JSON |
| `merge_cutlists.py` | Merges transcript cuts with auto-editor's silence/motion cuts |
| `main.py` | CLI orchestrator for the full pipeline |

## Tech Stack

- Python 3.8+ (system Python at `/opt/homebrew/bin/python3`)
- Dependencies: `auto-editor`, `whisperx`, `ffmpeg-python`
- WhisperX requires Python 3.12: use `/opt/homebrew/bin/python3.12` if needed
- FFmpeg must be installed (`brew install ffmpeg`)

## CLI Interface (target)

```bash
# Phase 1: Transcribe
python3 auto_transcript.py my_video.mp4

# Phase 2: User edits my_video.srt in text editor (delete blocks)

# Phase 3: Apply cuts + export
python3 main.py my_video.mp4 \
  --transcript my_video.srt \
  --whisper-json my_video.json \
  --margin 0.25 \
  --export final-cut-pro
```

### CLI Flags for `main.py`

- `--transcribe-only` — generate transcript, stop
- `--edit-transcript` — open SRT in default editor
- `--apply-transcript` — diff and inject cuts
- `--summary` — print edit stats (durations, blocks removed, % reduced)
- `--export` — `final-cut-pro`, `premiere`, `clip-sequence`, or `video`
- `--ffmpeg-args` — passthrough for re-encoding (e.g. `"-crf 22 -preset veryfast"`)
- `--margin` — padding around cuts (seconds)

## Design Constraints

- **JSON is source of truth** for timestamps — never trust user-edited SRT timestamps
- **Block-level deletion only** in v1 (no word-level, no reordering)
- **Non-monotonic/corrupt SRT timestamps** → log warning, skip block, don't crash
- FCPXML exports reference media by filename; original video must stay unmodified
- WhisperX timing variance: ±100ms

## Development Guidelines

- **Do NOT commit or push unless explicitly asked.** Wait for Tim to say when he wants a commit.
- Keep modules independent and testable — each file should work as a standalone unit
- SRT parsing must be robust against messy edits (extra blank lines, missing block numbers, etc.)
- Use `argparse` for CLI argument handling
- Write to stdout for status messages, stderr for warnings/errors
- No GUI — CLI-first, always
- Test with real video files when possible; keep test fixtures small

## Success Criteria (v1.0)

- Transcribe 10-min video with <5% word error rate
- Detect and apply 100% of deleted transcript blocks
- Export valid FCPXML importable by Final Cut Pro
- Process 10-min video in <2 minutes
- Zero crashes on malformed SRT input
