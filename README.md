# PaperCut

> **A wrapper for [auto-editor](https://github.com/WyattBlue/auto-editor) that enables transcript-based video editing.**

PaperCut extends `auto-editor` with a "Descript-like" workflow. Generate a transcript using **WhisperX**, delete unwanted text blocks, and automatically cut those sections from your video — all while preserving `auto-editor`'s silence and motion detection.

---

## Features

* **Hybrid Editing:** Combine automated silence removal with manual, text-based editorial choices.
* **WhisperX Integration:** Generates highly accurate, word-level timestamps using [WhisperX](https://github.com/m-bain/whisperx).
* **Web GUI:** Browser-based editor with two-column transcript view, video sync, and one-click export.
* **Auto-Edit Tools:** Clean filler words and deduplicate repeated takes automatically.
* **NLE Ready:** Exports to **FCPXML** (Final Cut Pro), **Premiere XML**, **DaVinci Resolve**, or standard video files.
* **CLI Pipeline:** Every step also works from the command line for scripting and automation.
* **Safe & Non-Destructive:** Uses WhisperX JSON as the timing source of truth — editing the transcript can't corrupt timestamps.

---

## Quick Start (Web GUI)

The web interface handles transcription, editing, and export in one place.

```bash
pip install -r requirements.txt
python3 web_gui.py
```

Open **http://localhost:5000** in your browser, then:

1. **Drop a video** (or paste a local path) — WhisperX transcribes it automatically
2. **Edit the transcript** — delete blocks, edit text, or use the auto-edit tools:
   - **Clean Fillers** removes blocks that are entirely filler words (um, uh, etc.)
   - **Dedupe Takes** finds consecutive similar blocks and keeps only the last
3. **Export** — choose your format and margin, click Export

All edits support undo/redo (Ctrl+Z / Ctrl+Shift+Z), auto-save to localStorage, and keyboard navigation (arrow keys, Delete, Ctrl+F to search).

---

## CLI Usage

The same pipeline is available as standalone scripts for automation or integration into other workflows.

### 1. Generate the Transcript

```bash
python3 auto_transcript.py my_video.mp4
```

Output: `my_video.json` (word-level timing) and `my_video.srt` (editable transcript). A backup `my_video.srt.orig` is also created.

### 2. Edit the Transcript

Open `my_video.srt` in any text editor. Delete entire blocks (index + timestamp + text) for sections you want to remove.

**Before editing:**
```srt
1
00:00:00,000 --> 00:00:02,100
Hey everyone, welcome back to the channel.

2
00:00:02,150 --> 00:00:04,800
Um, so today we're going to talk about...

3
00:00:05,210 --> 00:00:07,420
So today we're going to talk about editing.
```

**After editing (block 2 deleted):**
```srt
1
00:00:00,000 --> 00:00:02,100
Hey everyone, welcome back to the channel.

3
00:00:05,210 --> 00:00:07,420
So today we're going to talk about editing.
```

### 3. Apply Cuts and Export

```bash
python3 main.py my_video.mp4 \
  --transcript my_video.srt \
  --whisper-json my_video.json \
  --margin 0.25 \
  --export final-cut-pro
```

---

## Installation

**Prerequisites:**
* Python 3.8+
* [FFmpeg](https://ffmpeg.org/download.html)
* [WhisperX](https://github.com/m-bain/whisperx) (requires Python 3.12 on some systems)

**Setup:**

```bash
git clone https://github.com/timricchuiti/papercut.git
cd papercut
pip install -r requirements.txt
```

---

## Project Structure

```
papercut/
├── web_gui.py             # Web GUI server (primary interface)
├── auto_transcript.py     # WhisperX transcription wrapper
├── transcript_diff.py     # Diff engine — detects deleted blocks
├── merge_cutlists.py      # Builds auto-editor commands with cut ranges
├── main.py                # CLI orchestrator (full pipeline)
├── static/
│   └── index.html         # Web GUI frontend (single-file HTML/CSS/JS)
└── requirements.txt
```

---

## Troubleshooting

**Q: The cuts aren't appearing in my video**
- Make sure you're deleting the entire block (all 3–4 lines including the blank line)
- Verify the JSON file matches your video file

**Q: Audio sounds choppy**
- Try increasing the margin value (e.g., `--margin 0.5`)

**Q: WhisperX isn't installing**
- WhisperX may require Python 3.12: `pip install whisperx` with the correct Python version
- See the [WhisperX installation guide](https://github.com/m-bain/whisperx#setup)

---

## License

**MIT License**

This project wraps and builds upon:

* [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) (MIT)
* [m-bain/whisperx](https://github.com/m-bain/whisperx) (BSD-2-Clause)
