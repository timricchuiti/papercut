#!/usr/bin/env python3
"""Transcription wrapper — generates .json and .srt from a video file.

Supports two engines:
  - whisperx (default): Uses WhisperX CLI for transcription.
  - crisperwhisper: Uses CrisperWhisper (HuggingFace) for verbatim transcription
    that preserves filler words, stutters, and false starts.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def transcribe(video_path, model="medium", language="en", output_dir=None,
               engine="whisperx", progress_callback=None):
    """Run transcription on a video file to produce .json and .srt outputs.

    Args:
        video_path: Path to the input video file.
        model: WhisperX model size (default: medium). Ignored for CrisperWhisper.
        language: Language code (default: en).
        output_dir: Directory for output files (default: same as video).
        engine: Transcription engine — "whisperx" or "crisperwhisper".
        progress_callback: Optional callable(message) for progress updates.

    Returns:
        Tuple of (json_path, srt_path, orig_srt_path).
    """
    if engine == "crisperwhisper":
        return transcribe_crisper(video_path, language=language,
                                  output_dir=output_dir,
                                  progress_callback=progress_callback)

    # Default: WhisperX
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


def transcribe_crisper(video_path, language="en", output_dir=None,
                       progress_callback=None):
    """Run CrisperWhisper on a video file for verbatim transcription.

    CrisperWhisper preserves filler words (um, uh), stutters, false starts,
    and repetitions that standard Whisper models typically drop.

    Args:
        video_path: Path to the input video file.
        language: Language code (default: en).
        output_dir: Directory for output files (default: same as video).
        progress_callback: Optional callable(message) for progress updates.

    Returns:
        Tuple of (json_path, srt_path, orig_srt_path).
    """
    video = Path(video_path).resolve()
    if not video.exists():
        msg = f"Error: Video file not found: {video}"
        print(msg, file=sys.stderr)
        sys.exit(1)

    out_dir = Path(output_dir).resolve() if output_dir else video.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    def _progress(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    _progress("Loading CrisperWhisper model (nyrahealth/CrisperWhisper)...")

    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
    except ImportError as e:
        raise RuntimeError(
            f"CrisperWhisper requires additional dependencies: {e}\n"
            "Install with: pip install torch torchaudio "
            "git+https://github.com/nyrahealth/transformers.git@crisper_whisper"
        )

    model_id = "nyrahealth/CrisperWhisper"
    device = "cpu"
    torch_dtype = torch.float32

    _progress("Downloading/loading model weights (this may take a while on first run)...")
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        model_id, torch_dtype=torch_dtype,
        use_safetensors=True,
    )
    model.to(device)
    processor = AutoProcessor.from_pretrained(model_id)

    _progress("Setting up transcription pipeline...")
    pipe = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=torch_dtype,
        device=device,
    )

    _progress(f"Transcribing {video.name} (verbatim mode)...")
    result = pipe(
        str(video),
        return_timestamps="word",
        generate_kwargs={"language": language},
    )

    _progress("Processing transcription results...")

    # Convert CrisperWhisper output to WhisperX-compatible format
    chunks = result.get("chunks", [])
    if not chunks:
        raise RuntimeError("CrisperWhisper returned no transcription chunks.")

    # Build word list with timestamps
    words = []
    for chunk in chunks:
        ts = chunk.get("timestamp", (None, None))
        start_t, end_t = ts if ts else (None, None)
        if start_t is None or end_t is None:
            continue
        words.append({
            "word": chunk["text"].strip(),
            "start": round(float(start_t), 3),
            "end": round(float(end_t), 3),
        })

    if not words:
        raise RuntimeError("CrisperWhisper produced no words with valid timestamps.")

    # Group words into segments by pause gaps (>1s) or every ~30 words
    segments = _group_words_into_segments(words)

    whisperx_data = {"segments": segments}

    # Write JSON
    json_path = out_dir / f"{stem}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(whisperx_data, f, indent=2, ensure_ascii=False)

    # Write SRT
    srt_path = out_dir / f"{stem}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start_ts = _seconds_to_srt_time(seg["start"])
            end_ts = _seconds_to_srt_time(seg["end"])
            f.write(f"{i}\n{start_ts} --> {end_ts}\n{seg['text']}\n\n")

    # Save original SRT for diffing
    orig_srt_path = out_dir / f"{stem}.srt.orig"
    shutil.copy2(srt_path, orig_srt_path)

    _progress(f"Generated files:")
    _progress(f"  JSON (timestamps): {json_path}")
    _progress(f"  SRT (editable):    {srt_path}")
    _progress(f"  SRT (original):    {orig_srt_path}")
    _progress(f"Transcription complete ({len(segments)} segments, {len(words)} words).")

    return json_path, srt_path, orig_srt_path


def _group_words_into_segments(words, pause_threshold=1.0, max_words=30):
    """Group words into segments, splitting on pauses or word count.

    Args:
        words: List of {"word", "start", "end"} dicts.
        pause_threshold: Seconds of gap between words to trigger a new segment.
        max_words: Max words per segment before forcing a split.

    Returns:
        List of WhisperX-style segment dicts.
    """
    segments = []
    current_words = []

    for word in words:
        if current_words:
            gap = word["start"] - current_words[-1]["end"]
            if gap > pause_threshold or len(current_words) >= max_words:
                segments.append(_build_segment(current_words))
                current_words = []
        current_words.append(word)

    if current_words:
        segments.append(_build_segment(current_words))

    return segments


def _build_segment(words):
    """Build a WhisperX-compatible segment dict from a list of words."""
    text = " ".join(w["word"] for w in words)
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": text,
        "words": words,
    }


def _seconds_to_srt_time(seconds):
    """Convert seconds to SRT timestamp format HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    whole_s = int(s)
    ms = int(round((s - whole_s) * 1000))
    return f"{h:02d}:{m:02d}:{whole_s:02d},{ms:03d}"


def main():
    parser = argparse.ArgumentParser(
        description="Generate transcript from video using WhisperX or CrisperWhisper."
    )
    parser.add_argument("video", help="Path to the input video file")
    parser.add_argument("--model", default="medium",
                        help="WhisperX model size (default: medium)")
    parser.add_argument("--language", default="en",
                        help="Language code (default: en)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: same as video)")
    parser.add_argument("--engine", default="whisperx",
                        choices=["whisperx", "crisperwhisper"],
                        help="Transcription engine (default: whisperx)")

    args = parser.parse_args()
    transcribe(args.video, model=args.model, language=args.language,
               output_dir=args.output_dir, engine=args.engine)


if __name__ == "__main__":
    main()
