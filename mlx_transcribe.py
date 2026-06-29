#!/usr/bin/env python3
"""MLX transcription engine for PaperCut — the default engine.

Runs CrisperWhisper's weights through Apple's MLX framework, ~15-30x faster than
the transformers/MPS path (a 9-min file in ~30s vs ~30min) with no memory thrash.
Output matches the transformers engine: verbatim words + accurate word timestamps,
via two CrisperWhisper-specific pieces the naive MLX conversion drops —

  1. Alignment heads: the converted model doesn't carry CrisperWhisper's DTW
     alignment heads, so we set them from its config (median word-timestamp error
     drops from seconds to ~20ms).
  2. Word grouping: CrisperWhisper marks word boundaries with standalone space
     tokens (not a leading space on each word token, as stock Whisper does), so
     stock MLX over-splits words ("those" -> "tho se"). _crisper_split_to_word_
     tokens regroups on the space tokens, restoring whole words.

Runs under .venv-mlx; auto_transcript shells out to it for engine="mlx".
Emits the same .json / .srt / .srt.orig that transcribe_crisper does (it reuses
auto_transcript's segment + SRT builders), so the rest of PaperCut is unchanged.
"""
import json
import shutil
import sys
from pathlib import Path

import mlx.core as mx
import numpy as np
import mlx_whisper
from mlx_whisper import load_models
from mlx_whisper.tokenizer import Tokenizer

# Reuse PaperCut's segment/SRT formatting so MLX output is byte-format-identical
# to the CrisperWhisper engine's. (auto_transcript's heavy deps are imported
# lazily inside its functions, so importing the module here is cheap.)
from auto_transcript import _group_words_into_segments, _seconds_to_srt_time

MODEL_DIR = str(Path(__file__).resolve().parent / "models" / "crisper-mlx-fp16")

# CrisperWhisper's alignment heads (from its generation_config.json). Required —
# the MLX conversion drops them, and without them DTW word timestamps are garbage.
ALIGNMENT_HEADS = np.array(
    [[7, 0], [10, 17], [12, 18], [13, 12], [16, 1],
     [17, 14], [19, 11], [21, 4], [24, 1], [25, 6]],
    dtype=np.int32,
)


def _crisper_split_to_word_tokens(self, tokens):
    """CrisperWhisper-aware word grouping for MLX's timing pass.

    CrisperWhisper emits a standalone space token (one decoding to a lone ' ')
    between words; pieces with no space token between them belong to the same word
    (their own leading spaces stripped on join). Stock MLX splits on each token's
    leading space, which over-splits. Each separator is kept as a TRAILING token
    of the word it follows so a word's first token — and thus its DTW start time —
    is its first real piece, keeping timestamps tight.
    """
    def is_sep(tid):
        return tid < self.eot and self.decode([tid]) == " "

    words, word_tokens = [], []
    held = []                                  # leading separator with no word yet
    n = len(tokens)
    i = 0
    while i < n:
        t = tokens[i]
        if t >= self.eot:                      # special token -> its own entry
            words.append(self.decode([t]))
            word_tokens.append(held + [t])
            held = []
            i += 1
            continue
        if is_sep(t):
            if word_tokens:
                word_tokens[-1].append(t)      # trailing separator -> previous word
            else:
                held.append(t)                 # leading separator -> hold
            i += 1
            continue
        wt = held                              # absorb any held leading separator
        held = []
        while i < n and tokens[i] < self.eot and not is_sep(tokens[i]):
            wt.append(tokens[i])
            i += 1
        pieces = [p for p in wt if not is_sep(p)]
        text = " " + "".join(self.decode([p]).lstrip(" ") for p in pieces)
        words.append(text)
        word_tokens.append(wt)
    if held and word_tokens:                   # trailing separator at the very end
        word_tokens[-1].extend(held)
    return words, word_tokens


# Install the CrisperWhisper grouping (MLX's timing.find_alignment calls this).
Tokenizer.split_to_word_tokens = _crisper_split_to_word_tokens

_MODEL = None


def _ensure_model():
    """Load the MLX model once per process (with alignment heads) and reuse it."""
    global _MODEL
    if _MODEL is None:
        if not Path(MODEL_DIR, "model.safetensors").exists():
            raise FileNotFoundError(
                f"MLX model not found at {MODEL_DIR}. Run ./setup_mlx.sh first."
            )
        m = load_models.load_model(MODEL_DIR, dtype=mx.float16)
        m.set_alignment_heads(ALIGNMENT_HEADS)
        import importlib
        tmod = importlib.import_module("mlx_whisper.transcribe")
        tmod.ModelHolder.model = m
        tmod.ModelHolder.model_path = MODEL_DIR
        _MODEL = m
    return _MODEL


def transcribe_mlx(video_path, language="en", output_dir=None, progress_callback=None):
    """Transcribe a media file with MLX CrisperWhisper -> .json / .srt / .srt.orig.

    Returns (json_path, srt_path, orig_srt_path), matching transcribe_crisper.
    """
    video = Path(video_path).resolve()
    if not video.exists():
        print(f"Error: Video file not found: {video}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(output_dir).resolve() if output_dir else video.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    def _p(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)

    _p("Loading MLX CrisperWhisper model...")
    _ensure_model()

    _p(f"Transcribing {video.name} (MLX, verbatim)...")
    out = mlx_whisper.transcribe(
        str(video), path_or_hf_repo=MODEL_DIR,
        word_timestamps=True, language=language,
    )

    words = []
    for seg in out.get("segments", []):
        for w in seg.get("words", []):
            text = w.get("word", "").strip()
            if text and w.get("start") is not None and w.get("end") is not None:
                words.append({
                    "word": text,
                    "start": round(float(w["start"]), 3),
                    "end": round(float(w["end"]), 3),
                })
    if not words:
        raise RuntimeError("MLX produced no words with valid timestamps.")

    segments = _group_words_into_segments(words)
    data = {"segments": segments}

    json_path = out_dir / f"{stem}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    srt_path = out_dir / f"{stem}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n{_seconds_to_srt_time(seg['start'])} --> "
                    f"{_seconds_to_srt_time(seg['end'])}\n{seg['text']}\n\n")

    orig_srt_path = out_dir / f"{stem}.srt.orig"
    shutil.copy2(srt_path, orig_srt_path)

    _p(f"Generated files:")
    _p(f"  JSON (timestamps): {json_path}")
    _p(f"  SRT (editable):    {srt_path}")
    _p(f"  SRT (original):    {orig_srt_path}")
    _p(f"Transcription complete ({len(segments)} segments, {len(words)} words).")
    return json_path, srt_path, orig_srt_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MLX CrisperWhisper transcription.")
    parser.add_argument("video", help="Path to the input media file")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: same as video)")
    args = parser.parse_args()
    transcribe_mlx(args.video, language=args.language, output_dir=args.output_dir)
