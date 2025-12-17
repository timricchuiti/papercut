# Product Requirements Document: Transcript-Aware Auto-Editor Extension

**Project Title:** Transcript-Aware Auto-Editor Extension
**Purpose:** To extend the existing `auto-editor` tool with a minimal, "Descript-like" transcript editing layer. This allows users to manually delete text segments in a transcript file to remove them from the final video, while preserving `auto-editor`'s native ability to remove silence and motion.

---

## 1. 🧭 Strategic Goals

* **Hybrid Editing:** Combine automated silence/motion detection with manual, transcript-driven editorial decisions.
* **Workflow Integration:** Maintain full compatibility with `auto-editor`'s existing export ecosystem (FCPXML, Premiere, DaVinci Resolve) so it fits into professional NLE workflows.
* **CLI-First:** Remain a file-based, Command Line Interface tool that requires no real-time preview or complex GUI, keeping it lightweight and scriptable.
* **Simplicity:** The primary interaction model is **deletion**. Users simply delete lines from a text file to cut video.

---

## 2. 🧩 Key Features & Functional Requirements

### 2.1. Transcription Engine (WhisperX)
* **Backend:** Use [WhisperX](https://github.com/m-bain/whisperx) for high-accuracy speech-to-text.
* **Outputs:**
    * **Word-Level JSON:** A hidden "source of truth" file containing precise start/end timestamps for every word.
    * **User-Facing Transcript:** An editable `.srt` file (named to match the video, e.g., `my_video.srt`) formatted for human readability (sentence/line blocks).
* **Advanced Audio:** Support for optional speaker diarization and language detection provided by WhisperX.

### 2.2. The Editable Transcript Layer
* **User Interface:** The user’s preferred text editor.
* **Interaction:** Users open the `.srt` file and delete the specific blocks (timestamp + text) they wish to remove from the final video.
* **Granularity (v1.0):** Supports **block-level deletion** (full lines/sentences).
* **Constraint:** The system currently supports **deletion only**. Reordering, duplicating, or rewriting text is not supported in v1.

### 2.3. Transcript Diff & Logic Engine
* **Diffing:** The system compares the *edited* transcript against the *original* transcript.
* **Timestamp Extraction:** For every missing text block, the system retrieves the corresponding precise timestamps from the generated JSON.
* **Safeguard:** The system relies on the JSON for timing to prevent errors if the user accidentally modifies timestamps in the SRT file.

### 2.4. Combined Cutlist Engine
* **Merge Logic:** The system generates a union of two cutlists:
    1.  **Auto-Editor Cuts:** Generated via standard silence/motion detection algorithms.
    2.  **Transcript Cuts:** Generated via the diff engine.
* **Margins:** Respects standard `auto-editor` arguments like `--margin` and `--edit` when calculating the final ranges.

### 2.5. Reporting & Summary
* **CLI Summary:** When the `--summary` flag is used, output the following stats:
    * Total input duration.
    * Duration removed by silence detection.
    * Duration removed by transcript edits.
    * Number of transcript blocks removed.
    * Final output duration and percentage reduced.

---

## 3. ⚙️ System Architecture

### 3.1. Data Flow
```text
                       +----------------+
                       |  Original Video|
                       +----------------+
                               |
                               ▼
                  +------------------------+
                  |     WhisperX Engine    |
                  |  (word-level + SRT)    |
                  +------------------------+
                               |
                               ▼
               +-----------------------------+
               |     User Edits Transcript   |
               |       (deletes text)        |
               +-----------------------------+
                               |
                               ▼
        +------------------------------------------+
        | Diff Engine (compare original vs edited) |
        | Generate deleted timestamp ranges        |
        +------------------------------------------+
                               |
                               ▼
    +------------------------------------------------------+
    | Merge Engine                                         |
    | Combine:                                             |
    |   1. Auto-Editor’s timeline (silence, motion)        |
    |   2. Transcript-based cuts                           |
    +------------------------------------------------------+
                               |
                               ▼
        +--------------------------------------------+
        |      Auto-Editor Export (FCPXML, etc.)     |
        +--------------------------------------------+
```

*(Architecture flow derived from)*

### 3.2. Project Structure
| File | Description |
| --- | --- |
| `auto_transcript.py` | Wrapper for WhisperX transcript generation. |
| `transcript_diff.py` | Diffs original vs. edited transcript and generates cut ranges. |
| `merge_cutlists.py` | Merges transcript cuts with Auto‑Editor cuts. |
| `main.py` | CLI tool to orchestrate the full pipeline. |
| `export/` | Directory utilizing Auto‑Editor’s existing exporters. |

### 3.3. Tech Stack
* **Language:** Python 3.8+.
* **Core Dependencies:** `ffmpeg`, `whisperx`, `auto-editor` (latest version).

---

## 4. 🧰 CLI Workflow & UX
The tool operates in distinct phases: Generation, Editing, and Application.

### Phase 1: Generation
```bash
python3 auto_transcript.py my_video.mp4
# Optional: --output custom_name.srt
```

*Outputs: `my_video.json` (word timestamps) and `my_video.srt` (editable).*

### Phase 2: Editing
The user opens `my_video.srt` in a text editor.

* **Action:** Delete unwanted blocks.
* **Example:**
* *Before:*
```srt
3
00:00:05,210 --> 00:00:07,420
Um, so that’s the first thing we need to check.

```
* *After:* (User deletes the lines entirely).

### Phase 3: Application & Export
```bash
python3 main.py my_video.mp4 \
  --transcript my_video.srt \
  --whisper-json my_video.json \
  --margin 0.25 \
  --export final-cut-pro
```

### Supported CLI Flags
* `--transcribe-only`: Generate transcript but do not apply edits.
* `--edit-transcript`: Launch the system's default text editor for the transcript (optional convenience).
* `--apply-transcript`: Diff edited file against original and inject cuts.
* `--summary`: Output statistics of the edit (duration saved, etc.).
* `--export`: Choose format (`final-cut-pro`, `premiere`, `clip-sequence`, or `video` for re-encoding).
* `--ffmpeg-args`: Pass arguments for re-encoding (e.g., `"-crf 22 -preset veryfast"`).

---

## 5. 📏 Accuracy, Limitations & Safeguards

### 5.1. Known Limitations (v1.0)
* **Block-Level Only:** Partial deletions (removing one word from a sentence) are not supported yet; users must delete full timestamped blocks.
* **Accuracy:** WhisperX is generally accurate but may have a variance of **±100ms**.
* **Cut Boundaries:** Transcript cuts are not currently "snapped" to silence boundaries, which may result in jumpy cuts if the speaker is fast.
* **Single Source:** Only single-source videos are supported (no multicam).

### 5.2. Safeguards
* **Source of Truth:** If the user manually alters timestamps in the SRT, the system ignores them and uses the `my_video.json` for timing to prevent drift.
* **Corruption Handling:** If timestamps in the SRT are non-monotonic or corrupt, the tool logs a warning and skips that specific block rather than crashing.
* **Media Reference:** FCPXML exports reference media by filename. The original video file must remain unmodified between the transcription and export steps.

---

## 6. 🔮 Roadmap

### Short Term
* **Preview Clip:** A `--preview` flag to export a low-res video showing only the cut sections.
* **Word-Level Editing:** Support for deleting specific words within a paragraph block.
* **Snapping:** "Magnetic" cuts that snap transcript edit points to the nearest silence threshold to smooth out audio.

### Medium Term
* **GUI Editor:** A lightweight visual overlay for highlighting and deleting text.
* **Rich Markup:** Support for Markdown or HTML transcripts.
* **CLI Merge Review:** A terminal-based tool to review cut lines side-by-side.

### Long Term
* **"Mark" Mode:** Option to tag sections (e.g., "Bad Take") without deleting them, for NLE review.
* **Reordering:** Support for moving text blocks to reorder the video timeline.
* **Repeat/Duplicate:** Support for looping segments via text duplication.

---

## 7. 📜 License
MIT. This project wraps and builds on top of:

* [`auto-editor`](https://github.com/WyattBlue/auto-editor) (MIT).
* [`whisperx`](https://github.com/m-bain/whisperx) (BSD-2-Clause).

---

## 8. 📊 Success Criteria (v1.0)

- [ ] Successfully transcribe a 10-minute video with <5% word error rate
- [ ] Detect and apply 100% of deleted transcript blocks
- [ ] Export valid FCPXML that imports without errors in Final Cut Pro
- [ ] Process a 10-minute video in <2 minutes on standard hardware
- [ ] Zero crashes when handling malformed SRT edits