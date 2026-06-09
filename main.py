#!/usr/bin/env python3
"""CLI orchestrator for PaperCut — single-file transcript-based video editing.

For batches, use `batch.py`. This is the one-file equivalent:

    # Transcribe (verbatim by default):
    python3 main.py video.mp4 --transcribe-only

    # Open the SRT to edit, then export to Final Cut Pro:
    python3 main.py video.mp4 --edit-transcript
    python3 main.py video.mp4 --export final-cut-pro

Run under the CrisperWhisper venv (`.venv-crisper/bin/python`) for transcription.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from auto_transcript import transcribe
from papercut_core import export_from_srt


def open_in_editor(filepath):
    """Open a file in the user's default editor."""
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL"))
    if editor:
        subprocess.run([editor, str(filepath)])
    elif sys.platform == "darwin":
        subprocess.run(["open", "-t", str(filepath)])
    elif sys.platform.startswith("linux"):
        subprocess.run(["xdg-open", str(filepath)])
    else:
        print(f"Please open {filepath} in your text editor.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="PaperCut — transcript-based video editing (single file).",
        epilog="Example: python3 main.py video.mp4 --export final-cut-pro",
    )
    parser.add_argument("video", help="Path to the input video/audio file")

    # Transcript inputs (default to companions next to the media)
    parser.add_argument("--transcript", help="Path to the edited .srt (default: <video>.srt)")
    parser.add_argument("--whisper-json", help="Path to the .json (default: <video>.json)")
    parser.add_argument("--original-srt", help="Path to the .srt.orig (default: <transcript>.orig)")

    # Workflow
    parser.add_argument("--transcribe-only", action="store_true",
                        help="Generate transcript and stop")
    parser.add_argument("--edit-transcript", action="store_true",
                        help="Open the SRT in your default text editor")

    # Export
    parser.add_argument("--export", default=None,
                        choices=["final-cut-pro", "resolve", "premiere", "video"],
                        help="Export format")
    parser.add_argument("--margin", type=float, default=0.1,
                        help="Boundary padding in seconds (default: 0.1)")
    parser.add_argument("--threshold", type=float, default=0.04,
                        help="Silence amplitude threshold (default: 0.04)")
    parser.add_argument("--ffmpeg-args", default=None,
                        help='FFmpeg args for --export video (e.g. "-crf 22 -preset veryfast")')
    parser.add_argument("--output", default=None, help="Explicit output path")

    # Transcription options
    parser.add_argument("--engine", default="crisperwhisper",
                        choices=["crisperwhisper", "whisperx"],
                        help="Transcription engine (default: crisperwhisper)")
    parser.add_argument("--model", default="medium",
                        help="WhisperX model size (ignored for CrisperWhisper)")
    parser.add_argument("--language", default="en", help="Language code")
    parser.add_argument("--output-dir", default=None, help="Transcription output directory")

    args = parser.parse_args()

    video = Path(args.video).resolve()
    if not video.exists():
        print(f"Error: media file not found: {video}", file=sys.stderr)
        sys.exit(1)

    if args.transcribe_only:
        transcribe(str(video), model=args.model, language=args.language,
                   output_dir=args.output_dir, engine=args.engine)
        return

    edited_srt = Path(args.transcript).resolve() if args.transcript else video.with_suffix(".srt")

    if args.edit_transcript:
        if not edited_srt.exists():
            print(f"Error: SRT not found: {edited_srt}\nRun --transcribe-only first.",
                  file=sys.stderr)
            sys.exit(1)
        open_in_editor(edited_srt)
        print("Editor closed. Re-run with --export to apply cuts.")
        return

    if not args.export:
        parser.error("Nothing to do — pass --transcribe-only, --edit-transcript, or --export.")

    whisper_json = (Path(args.whisper_json).resolve() if args.whisper_json
                    else video.with_suffix(".json"))
    orig_srt = (Path(args.original_srt).resolve() if args.original_srt
                else Path(str(edited_srt) + ".orig"))

    if not edited_srt.exists():
        print(f"Error: edited SRT not found: {edited_srt}", file=sys.stderr)
        sys.exit(1)

    result = export_from_srt(
        str(video), str(edited_srt),
        whisper_json=str(whisper_json) if whisper_json.exists() else None,
        orig_srt=str(orig_srt) if orig_srt.exists() else None,
        export_format=args.export, margin=args.margin, threshold=args.threshold,
        ffmpeg_args=args.ffmpeg_args, output_path=args.output,
    )

    src = result.get("source_duration") or 0.0
    kept = result.get("total_duration") or 0.0
    reduction = (1 - kept / src) * 100 if src > 0 else 0.0
    print(result["message"])
    print(f"  kept {kept:.1f}s of {src:.1f}s ({reduction:.0f}% removed)")
    print(f"  output: {result['output_path']}")
    for w in result.get("warnings", []):
        print(f"  ⚠ WARNING: {w}", file=sys.stderr)


if __name__ == "__main__":
    main()
