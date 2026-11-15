# PaperCut

Transcript-based editing for video and audio. Transcribe a recording **verbatim**
(fillers, stutters and false starts kept, because those are what gets cut), mark
the cuts as text, and export a non-destructive cut list for Final Cut Pro or
Premiere, or a re-encoded file. Silence between the kept words is removed
automatically; the transcript edits and the silence cuts compose into one clip
list that references the original media.

Default engine: **CrisperWhisper running on Apple MLX** (`mlx_transcribe.py`),
15–30× faster than the transformers path with the same verbatim words and
word-accurate timestamps. WhisperX is the text witness for the two-pass check
and the non-verbatim fallback. No auto-editor dependency.

---

## Setup (macOS, Apple silicon)

```bash
brew install ffmpeg
./setup_mlx.sh                      # builds .venv-mlx and converts CrisperWhisper → MLX fp16 (~3 GB, once)

/opt/homebrew/bin/python3.12 -m venv .venv-crisper     # export/CLI venv
.venv-crisper/bin/python -m pip install torch torchaudio accelerate safetensors librosa soundfile numpy
.venv-crisper/bin/python -m pip install "git+https://github.com/nyrahealth/transformers.git@crisper_whisper"

pipx install whisperx               # optional: witness / fallback engine
```

`batch.py` and `main.py` run under `.venv-crisper`; transcription shells out to
`.venv-mlx` for the default engine. Do not `pip install mlx-whisper` (the pinned
mlx-examples commit is required; see `setup_mlx.sh`). The browser GUI uses the
system `python3` with Flask (`pip install flask`).

---

## Workflow

### 1. Transcribe

```bash
P=.venv-crisper/bin/python
$P main.py video.mp4 --transcribe-only --output-dir out/          # MLX (default)
$P main.py video.mp4 --transcribe-only --engine whisperx --model large-v3 --output-dir wx/   # witness
$P batch.py transcribe <dir>                                        # every media file in a directory
```

Produces `<stem>.json` (word timings, the source of truth), `<stem>.srt`
(editable) and `<stem>.srt.orig` (untouched copy). The transcriber audits
itself: zero-width ghost words are marked, words decoded over room tone are
dropped, quiet words are listed.

### 2. Edit the SRT

Wrap the words to remove in `[[CUT]] … [[/CUT]]`; a span may run across
blocks. Never change the spelling of a word that stays — a changed word is
treated as a cut. Keep the LAST take of a repeat. `[[FLAG: note]]` in a block
becomes a marker on the exported clip. Full rules: `EDITING_PROTOCOL.md`.

```srt
2
00:00:02,150 --> 00:00:07,420
[[CUT]]So today we're going to[[/CUT]] so today we're going to talk about editing.
```

### 3. Export

```bash
$P main.py video.mp4 --export final-cut-pro \
   --transcript out/video.srt --original-srt out/video.srt.orig \
   --whisper-json out/video.json --output cuts/video_ALTERED.fcpxml
$P batch.py export <dir>          # FCPXML next to each edited source
$P batch.py status <dir>          # transcribed / edited / exported per file
```

The FCP project is named after the **output** file's stem, so several cuts of
one source import as distinct projects. Formats: `final-cut-pro`, `resolve`,
`premiere`, `video` (ffmpeg re-encode).

Cut knobs (defaults are the tuned ones; change them only on request):

| Flag | Default | Does |
|---|---|---|
| `--margin` | 0.07 | edge breath around kept speech, applied once; negative erodes tighter |
| `--bridge` | 0.20 | silent gaps up to ~2× this inside speech are kept so sentences don't fragment |
| `--snap` | 0.5 | a kept edge still inside loud audio moves out to the loud edge (max seconds) |
| `--sliver` | 0.25 | isolated no-word clips shorter than this (clicks, transients) are dropped |
| `--threshold` | 0.04 | silence amplitude threshold |

### 4. Verify

`tools/` holds the post-export helpers (witness diff, uncovered-audio scan,
word dump, clip list, edge scan, setin/setout/dropclips/addclip/moveclips,
breaths, RMS profile, cut audio/AV render, audio-delay sync variant). See
`tools/README.md`.

### Browser GUI

```bash
python3 web_gui.py --port 5009
```

Two-pane editor (original | edit) with cut/restore, drag reorder, Free Edit
mode, auto-edit tools, undo/redo and export presets. Never port 5000 (macOS
AirPlay).

---

## Tests

```bash
.venv-crisper/bin/python -m unittest test_papercut_core
```

---

## Layout

```
papercut/
├── main.py                single-file CLI (transcribe / export)
├── batch.py               headless driver over a directory
├── papercut_core.py       shared export core: cut placement, silence, snap, sliver
├── auto_transcript.py     engine dispatcher (mlx / crisperwhisper / whisperx) + audit
├── mlx_transcribe.py      MLX engine (runs under .venv-mlx)
├── silence.py             silence detection, margin, bridging
├── timeline_export.py     clip list, FCPXML / Premiere XML / ffmpeg export
├── transcript_diff.py     SRT parser, word JSON loader
├── web_gui.py + static/   browser editor
├── tools/                 post-export + verification helpers
├── setup_mlx.sh           one-time MLX engine setup
├── EDITING_PROTOCOL.md    how the SRT is edited
├── PRD.md                 design document
└── CLAUDE.md              working notes for the agent
```

## License

MIT. Builds on [nyrahealth/CrisperWhisper](https://github.com/nyrahealth/CrisperWhisper),
[ml-explore/mlx-examples](https://github.com/ml-explore/mlx-examples) (whisper) and
[m-bain/whisperx](https://github.com/m-bain/whisperx).
