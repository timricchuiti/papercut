# 🛠️ Transcript-Aware Auto-Editor Extension

> **A wrapper for [auto-editor](https://github.com/WyattBlue/auto-editor) that enables transcript-based video editing.**

This tool extends `auto-editor` with a "Descript-like" workflow. It allows you to generate a transcript using **WhisperX**, manually delete unwanted text blocks from a `.srt` file, and then automatically cut those sections from your video—all while preserving `auto-editor`'s powerful silence and motion detection capabilities.

---

## ✨ Features

* **Hybrid Editing:** Combine automated silence removal with manual, text-based editorial choices.
* **WhisperX Integration:** Generates highly accurate, word-level timestamps using [WhisperX](https://github.com/m-bain/whisperx).
* **Simple Workflow:** Just delete lines from a text file to cut them from the video.
* **NLE Ready:** Exports to **FCPXML** (Final Cut Pro), **Premiere XML**, **DaVinci Resolve**, or standard video files.
* **Safe & Non-Destructive:** Uses a hidden JSON "source of truth" for timing to prevent errors if you accidentally mess up timestamps in the transcript.

---

## 🔍 How It Works

1. **WhisperX** generates word-level timestamps (JSON) and a human-readable transcript (SRT)
2. You edit the SRT file by deleting unwanted text blocks
3. The diff engine compares your edits against the original
4. Your deletions are merged with auto-editor's silence detection
5. Export to your preferred format (FCPXML, Premiere, video file)

---

## 🏗️ Development Usage

Since this project is in active development, you will run the scripts directly via Python.

### 1. Generate the Transcript
Run the transcription script on your video file:
```bash
python3 auto_transcript.py my_video.mp4
```

*Output: `my_video.json` (timing data) and `my_video.srt` (editable).*

### 2. Edit the Transcript
Open `my_video.srt` in any text editor (or whatever your video filename is, with `.srt` extension). **Delete the full blocks** (timestamp + text) for any sections you want to remove.

**Example - Before editing:**
```srt
1
00:00:00,000 --> 00:00:02,100
Hey everyone, welcome back to the channel.

2
00:00:02,150 --> 00:00:04,800
Um, so today we're going to talk about...

3
00:00:05,210 --> 00:00:07,420
Um, so that's the first thing we need to check.
```

**After editing (block 2 deleted):**
```srt
1
00:00:00,000 --> 00:00:02,100
Hey everyone, welcome back to the channel.

3
00:00:05,210 --> 00:00:07,420
Um, so that's the first thing we need to check.
```

### 3. Apply the Cuts
Run the main script to merge your edits:
```bash
python3 main.py my_video.mp4 \
  --transcript my_video.srt \
  --whisper-json my_video.json \
  --margin 0.25 \
  --export final-cut-pro
```

---

## 📦 Installation

**Prerequisites:**
* Python 3.8+
* [FFmpeg](https://ffmpeg.org/download.html)

**Setup:**

1. Clone the repository:
```bash
git clone https://github.com/YOUR_USERNAME/transcript-aware-auto-editor.git
cd transcript-aware-auto-editor
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

---

## 📁 Project Structure
```
transcript-aware-auto-editor/
├── auto_transcript.py      # Generate transcripts using WhisperX
├── transcript_diff.py      # Diff engine for detecting deletions
├── merge_cutlists.py       # Merge transcript cuts with auto-editor cuts
├── main.py                 # Main CLI orchestrator
├── requirements.txt        # Python dependencies
└── README.md
```

---

## 🔮 Roadmap
* [ ] **Preview Mode:** Generate a low-res preview video of the cuts before exporting.
* [ ] **Word-Level Editing:** Support for deleting individual words within a sentence block.
* [ ] **Magnetic Cuts:** "Snap" transcript edits to the nearest silence threshold for smoother audio transitions.
* [ ] **GUI Assistant:** A simple visual tool to highlight and delete text without opening a text editor.

---

## 🔧 Troubleshooting

**Q: The cuts aren't appearing in my video**
- Make sure you're deleting the entire block (all 3-4 lines including the blank line)
- Verify the JSON file matches your video file

**Q: Audio sounds choppy**
- Try increasing the `--margin` value (e.g., `--margin 0.5`)

**Q: WhisperX isn't installing**
- Check that you have CUDA/GPU support if using GPU acceleration
- See the [WhisperX installation guide](https://github.com/m-bain/whisperx#setup)

---

##📜 License
**MIT License**

This project wraps and builds upon the incredible work of:

* [WyattBlue/auto-editor](https://github.com/WyattBlue/auto-editor) (MIT)
* [m-bain/whisperx](https://github.com/m-bain/whisperx) (BSD-2-Clause)