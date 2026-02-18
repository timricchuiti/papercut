#!/usr/bin/env python3
"""WhisperX wrapper — generates .json and .srt from a video file."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def transcribe(video_path, model="medium", language="en", output_dir=None):
    """Run WhisperX on a video file to produce .json and .srt outputs.

    Args:
        video_path: Path to the input video file.
        model: WhisperX model size (default: medium).
        language: Language code (default: en).
        output_dir: Directory for output files (default: same as video).

    Returns:
        Tuple of (json_path, srt_path, orig_srt_path).
    """
    video = Path(video_path).resolve()
    if not video.exists():
        print(f"Error: Video file not found: {video}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(output_dir).resolve() if output_dir else video.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = video.stem

    cmd = [
        "whisperx",
        str(video),
        "--model", model,
        "--language", language,
        "--output_format", "all",
        "--compute_type", "float32",
        "--output_dir", str(out_dir),
    ]

    print(f"Running WhisperX on {video.name}...")
    print(f"  Command: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error: WhisperX failed (exit code {result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)

    if result.stdout:
        print(result.stdout)

    json_path = out_dir / f"{stem}.json"
    srt_path = out_dir / f"{stem}.srt"

    for path, label in [(json_path, "JSON"), (srt_path, "SRT")]:
        if not path.exists():
            print(f"Error: Expected {label} output not found: {path}", file=sys.stderr)
            sys.exit(1)

    # Save a copy of the original SRT for diffing later
    orig_srt_path = out_dir / f"{stem}.srt.orig"
    shutil.copy2(srt_path, orig_srt_path)

    print(f"\nGenerated files:")
    print(f"  JSON (timestamps): {json_path}")
    print(f"  SRT (editable):    {srt_path}")
    print(f"  SRT (original):    {orig_srt_path}")
    print(f"\nEdit {srt_path} to remove unwanted sections, then run main.py to apply cuts.")

    return json_path, srt_path, orig_srt_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate transcript from video using WhisperX."
    )
    parser.add_argument("video", help="Path to the input video file")
    parser.add_argument("--model", default="medium", help="WhisperX model size (default: medium)")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: same as video)")

    args = parser.parse_args()
    transcribe(args.video, model=args.model, language=args.language, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
