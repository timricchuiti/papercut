#!/usr/bin/env python3
"""Transcription wrapper — generates .json and .srt from a video file.

Dispatches to one of three engines (see transcribe()):
  - mlx (default): CrisperWhisper's weights via Apple MLX (mlx_transcribe.py),
    verbatim and ~15-30x faster.
  - crisperwhisper: CrisperWhisper (HuggingFace transformers), verbatim.
  - whisperx: the WhisperX CLI.
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def transcribe(video_path, model="medium", language="en", output_dir=None,
               engine="mlx", progress_callback=None, device="auto"):
    """Run transcription on a video file to produce .json and .srt outputs.

    Args:
        video_path: Path to the input video file.
        model: WhisperX model size (default: medium). Ignored for the others.
        language: Language code (default: en).
        output_dir: Directory for output files (default: same as video).
        engine: Transcription engine — "mlx" (default, verbatim + ~15-30x faster),
            "crisperwhisper" (verbatim transformers/MPS), or "whisperx".
        progress_callback: Optional callable(message) for progress updates.

    Returns:
        Tuple of (json_path, srt_path, orig_srt_path).
    """
    if engine == "mlx":
        return _transcribe_mlx(video_path, language=language,
                               output_dir=output_dir,
                               progress_callback=progress_callback)
    if engine == "crisperwhisper":
        return transcribe_crisper(video_path, language=language,
                                  output_dir=output_dir,
                                  progress_callback=progress_callback,
                                  device=device)
    return _transcribe_whisperx(video_path, model=model, language=language,
                                output_dir=output_dir)


def _make_progress(progress_callback):
    """Return a progress(msg) that prints and forwards to an optional callback."""
    def _progress(msg):
        print(msg)
        if progress_callback:
            progress_callback(msg)
    return _progress


def _rms_profile(media_path, hop_s=0.05, sample_rate=16000):
    """RMS per `hop_s` window of the media's mono audio (ffmpeg -> numpy)."""
    import numpy as np
    cmd = ["ffmpeg", "-v", "error", "-i", str(media_path), "-vn", "-ac", "1",
           "-ar", str(sample_rate), "-f", "s16le", "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, timeout=900).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(sample_rate * hop_s)
    m = len(x) // n
    if m == 0:
        return np.zeros(0, dtype=np.float32), hop_s
    return np.sqrt((x[:m * n].reshape(m, n) ** 2).mean(1)), hop_s


# A word whose span (± AUDIT_SLACK_S) never rises above this RMS (16 kHz mono,
# 50 ms windows) was decoded over room tone — a hallucination ("The.", "Okay.",
# "hm-hmm") or a phrase timed into a silence. Calibrated on four recordings
# (2026-10-20): room tone peaks at 0.0004-0.0011, normal speech at 0.1-0.25, and
# muttered under-the-breath speech as low as 0.0018 — so the bar sits just above
# room tone and must stay there; it must never catch quiet real speech. Words
# between the bar and AUDIT_QUIET_RMS are kept but listed as "quiet" for review.
AUDIT_SILENT_RMS = 0.0015
AUDIT_QUIET_RMS = 0.01
AUDIT_SLACK_S = 0.1


def audit_words(words, media_path, silent_rms=AUDIT_SILENT_RMS, slack_s=AUDIT_SLACK_S,
                quiet_rms=AUDIT_QUIET_RMS):
    """Post-pass over a verbatim transcriber's word list for its two known failures.

    - **Ghosts**: zero-width words (start == end) where the decoder stalled. The
      text is sometimes a loop of earlier words and sometimes real speech squashed
      to a point, so they are KEPT, marked ``"ghost": True`` and reported.
    - **Silence hallucinations**: words of real width whose span (± slack) holds
      no audio above ``silent_rms`` — DROPPED from the transcript and reported.
      Pause-stretched words are safe: the span includes the spoken part. A run
      of several consecutive dropped words is usually a REAL phrase the decoder
      timed into a silence (the audio is elsewhere; papercut_uncovered.py finds
      it), so the report says so.
    - **Quiet words**: kept, but listed when the peak is under ``quiet_rms`` —
      muttered asides and faint hallucinations live there; the editor decides.

    Returns (kept_words, audit) where audit = {"ghost_runs": [...],
    "dropped_silent": [...], "quiet": [...], "silent_rms": float}. Audio problems
    (no ffmpeg, unreadable media) disable the silence check rather than failing.
    """
    try:
        rms, hop = _rms_profile(media_path)
    except Exception as e:                                  # noqa: BLE001
        print(f"  audit: RMS profile unavailable ({e}); silence check skipped",
              file=sys.stderr)
        rms, hop = None, 0.05

    kept, dropped, ghost_runs, quiet = [], [], [], []
    run = None
    for w in words:
        if w["end"] <= w["start"]:
            w["ghost"] = True
            if run is None or run["start"] != w["start"]:
                run = {"start": w["start"], "text": w["word"], "count": 1}
                ghost_runs.append(run)
            else:
                run["text"] += " " + w["word"]
                run["count"] += 1
            kept.append(w)
            continue
        run = None
        if rms is not None and len(rms):
            i0 = max(0, int((w["start"] - slack_s) / hop))
            i1 = min(len(rms), int((w["end"] + slack_s) / hop) + 1)
            peak = float(rms[i0:i1].max()) if i1 > i0 else 0.0
            if peak < silent_rms:
                dropped.append({"start": w["start"], "end": w["end"],
                                "word": w["word"], "peak_rms": round(peak, 4)})
                continue
            if peak < quiet_rms:
                quiet.append({"start": w["start"], "end": w["end"],
                              "word": w["word"], "peak_rms": round(peak, 4)})
        kept.append(w)
    return kept, {"ghost_runs": ghost_runs, "dropped_silent": dropped, "quiet": quiet,
                  "silent_rms": silent_rms, "quiet_rms": quiet_rms}


def _runs(items, max_gap=0.05):
    """Group consecutive audit entries (end of one ~ start of next) into runs."""
    runs = []
    for it in items:
        if runs and it["start"] - runs[-1]["end"] <= max_gap:
            runs[-1]["end"] = it["end"]; runs[-1]["words"].append(it)
        else:
            runs.append({"start": it["start"], "end": it["end"], "words": [it]})
    return runs


def _fmt_t(t):
    return f"{int(t // 60)}:{t % 60:05.2f}"


def _write_transcript_outputs(segments, out_dir, stem, progress=print, audit=None):
    """Write {stem}.json / .srt / .srt.orig from WhisperX-style segments.

    Shared by the CrisperWhisper and MLX engines so their on-disk output is
    identical. Returns (json_path, srt_path, orig_srt_path).
    """
    out_dir = Path(out_dir)
    json_path = out_dir / f"{stem}.json"
    payload = {"segments": segments}
    if audit is not None:
        payload["audit"] = audit
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    srt_text = "".join(
        f"{i}\n{_seconds_to_srt_time(seg['start'])} --> "
        f"{_seconds_to_srt_time(seg['end'])}\n{seg['text']}\n\n"
        for i, seg in enumerate(segments, 1)
    )
    srt_path = out_dir / f"{stem}.srt"
    srt_path.write_text(srt_text, encoding="utf-8")
    orig_srt_path = out_dir / f"{stem}.srt.orig"          # original, for diffing
    orig_srt_path.write_text(srt_text, encoding="utf-8")

    progress("Generated files:")
    progress(f"  JSON (timestamps): {json_path}")
    progress(f"  SRT (editable):    {srt_path}")
    progress(f"  SRT (original):    {orig_srt_path}")
    if audit is not None:
        ghosts, dropped, quiet = audit["ghost_runs"], audit["dropped_silent"], audit.get("quiet", [])
        progress(f"Audit: {len(ghosts)} ghost run(s) kept + marked, "
                 f"{len(dropped)} silent word(s) dropped, {len(quiet)} quiet word(s) to check")
        for r in ghosts:
            progress(f"  ghost   {_fmt_t(r['start'])}  {r['text']!r}")
        for r in _runs(dropped):
            text = " ".join(w["word"] for w in r["words"])
            peak = max(w["peak_rms"] for w in r["words"])
            note = ("  <- a run: probably real speech timed into a silence; "
                    "find the audio with papercut_uncovered.py" if len(r["words"]) >= 3 else "")
            progress(f"  dropped {_fmt_t(r['start'])}-{_fmt_t(r['end'])}  {text!r} (peak rms {peak}){note}")
        for r in _runs(quiet):
            text = " ".join(w["word"] for w in r["words"])
            peak = max(w["peak_rms"] for w in r["words"])
            progress(f"  quiet   {_fmt_t(r['start'])}-{_fmt_t(r['end'])}  {text!r} (peak rms {peak})")
    return json_path, srt_path, orig_srt_path


def _transcribe_whisperx(video_path, model="medium", language="en", output_dir=None):
    """Transcribe via the WhisperX CLI -> .json / .srt / .srt.orig."""
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


def _transcribe_mlx(video_path, language="en", output_dir=None, progress_callback=None):
    """Transcribe via the MLX engine (the default) — ~15-30x faster than the rest.

    MLX's deps are incompatible with the torch/transformers env used elsewhere, so
    this shells out to mlx_transcribe.py in the dedicated .venv-mlx. Raises if MLX
    isn't set up (run ./setup_mlx.sh, or pass --engine crisperwhisper).
    """
    here = Path(__file__).resolve().parent
    mlx_py = here / ".venv-mlx" / "bin" / "python"
    script = here / "mlx_transcribe.py"
    if not mlx_py.exists() or not (here / "models" / "crisper-mlx-fp16" / "model.safetensors").exists():
        raise RuntimeError(
            "MLX engine not set up (.venv-mlx / converted model missing). "
            "Run ./setup_mlx.sh, or use --engine crisperwhisper.")

    video = Path(video_path).resolve()
    out_dir = Path(output_dir).resolve() if output_dir else video.parent

    _progress = _make_progress(progress_callback)
    _progress(f"Transcribing {video.name} via MLX engine...")
    cmd = [str(mlx_py), str(script), str(video),
           "--language", language, "--output-dir", str(out_dir)]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError(f"MLX transcription failed (exit {result.returncode})")

    stem = video.stem
    return (out_dir / f"{stem}.json",
            out_dir / f"{stem}.srt",
            out_dir / f"{stem}.srt.orig")


def _pick_device(prefer="auto"):
    """Choose a torch device. 'auto' -> Apple-Silicon GPU (mps) if available."""
    import torch
    if prefer and prefer != "auto":
        return prefer
    try:
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


_CRISPER_MODEL_ID = "nyrahealth/CrisperWhisper"

# Per-process cache of the built ASR pipeline, keyed by (device, dtype). An
# in-process batch (batch.py --in-process) reuses the loaded weights AND the
# warm MPS kernels across files, skipping the cold-start tax every file would
# otherwise pay. A subprocess-per-file run never hits this (fresh process each).
_CRISPER_PIPE_CACHE = {}


def _get_crisper_pipe(device="auto", progress=print):
    """Build (and per-process cache) the CrisperWhisper ASR pipeline.

    Returns (pipe, resolved_device). The first call loads the model and compiles
    GPU kernels; later calls in the same process reuse both.
    """
    try:
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
    except ImportError as e:
        # The CrisperWhisper transformers fork pins strict dependency ranges that
        # may not match newer tokenizers/huggingface-hub versions. If the error is
        # just a version-range complaint, bypass the check and retry.
        if "is required for a normal functioning" in str(e):
            progress("Bypassing transformers version check (compatible fork detected)...")
            import importlib.util as _ilu, sys as _sys
            _spec = _ilu.find_spec("transformers.utils.versions")
            _vmod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_vmod)
            _vmod.require_version_core = lambda *a, **kw: None
            _sys.modules["transformers.utils.versions"] = _vmod
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
        else:
            raise RuntimeError(
                f"CrisperWhisper requires additional dependencies: {e}\n"
                "Install with: pip install torch torchaudio "
                "git+https://github.com/nyrahealth/transformers.git@crisper_whisper"
            )

    device = _pick_device(device)
    torch_dtype = torch.float32  # float16 is unreliable for timestamps on mps
    cache_key = (device, str(torch_dtype))
    cached = _CRISPER_PIPE_CACHE.get(cache_key)
    if cached is not None:
        progress(f"Reusing loaded CrisperWhisper model (device: {device}, warm).")
        return cached, device

    progress(f"Using device: {device}")
    progress("Downloading/loading model weights (this may take a while on first run)...")
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        _CRISPER_MODEL_ID, torch_dtype=torch_dtype,
        use_safetensors=True,
    )
    model.to(device)
    processor = AutoProcessor.from_pretrained(_CRISPER_MODEL_ID)

    progress("Setting up transcription pipeline...")
    pipe = pipeline(
        "automatic-speech-recognition",
        model=model,
        tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor,
        torch_dtype=torch_dtype,
        device=torch.device(device),
    )

    # Legacy compatibility patch: some older transformers returned tuples in
    # generate() outputs that the CrisperWhisper fork couldn't .cpu(). Modern
    # transformers (>=4.x) handle this natively via a _postprocess_outputs that
    # takes a `decoder_input_ids` argument — applying the old patch there breaks
    # generation, so only patch when the native method lacks that parameter.
    try:
        import inspect as _inspect
        from transformers.models.whisper import generation_whisper as _gw

        _native_params = _inspect.signature(
            _gw.WhisperGenerationMixin._postprocess_outputs
        ).parameters
        _needs_patch = "decoder_input_ids" not in _native_params

        def _patched_postprocess(self_model, seek_outputs, return_token_timestamps, generation_config):
            if return_token_timestamps and hasattr(generation_config, "alignment_heads"):
                num_frames = getattr(generation_config, "num_frames", None)
                seek_outputs["token_timestamps"] = self_model._extract_token_timestamps(
                    seek_outputs, generation_config.alignment_heads, num_frames=num_frames
                )

            if generation_config.return_dict_in_generate:
                def split_by_batch_index(values, key, batch_idx):
                    if key == "scores":
                        return [v[batch_idx].cpu() for v in values]
                    if key == "past_key_values":
                        return None
                    # Handle tuples that lack .cpu() — convert element to tensor first
                    val = values[batch_idx]
                    if isinstance(val, tuple):
                        try:
                            val = torch.stack(val)
                        except Exception:
                            return val
                    if hasattr(val, 'cpu'):
                        return val.cpu()
                    return val

                sequence_tokens = seek_outputs["sequences"]
                seek_outputs = [
                    {k: split_by_batch_index(v, k, i) for k, v in seek_outputs.items()}
                    for i in range(sequence_tokens.shape[0])
                ]
            else:
                sequence_tokens = seek_outputs

            return sequence_tokens, seek_outputs

        if _needs_patch:
            _gw.WhisperGenerationMixin._postprocess_outputs = _patched_postprocess
    except Exception:
        pass  # If patching fails, proceed anyway — may work without it

    _CRISPER_PIPE_CACHE[cache_key] = pipe
    return pipe, device


def transcribe_crisper(video_path, language="en", output_dir=None,
                       progress_callback=None, device="auto", chunk_length_s=30):
    """Run CrisperWhisper on a video file for verbatim transcription.

    CrisperWhisper preserves filler words (um, uh), stutters, false starts,
    and repetitions that standard Whisper models typically drop.

    Args:
        video_path: Path to the input video file.
        language: Language code (default: en).
        output_dir: Directory for output files (default: same as video).
        progress_callback: Optional callable(message) for progress updates.
        device: 'auto' (default; uses Apple-Silicon GPU/mps when available),
            or an explicit torch device string like 'cpu', 'mps', 'cuda'.

    Returns:
        Tuple of (json_path, srt_path, orig_srt_path).
    """
    # Let unsupported ops fall back to CPU instead of erroring on MPS.
    import os as _os
    _os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    video = Path(video_path).resolve()
    if not video.exists():
        msg = f"Error: Video file not found: {video}"
        print(msg, file=sys.stderr)
        sys.exit(1)

    out_dir = Path(output_dir).resolve() if output_dir else video.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    _progress = _make_progress(progress_callback)
    _progress("Loading CrisperWhisper model (nyrahealth/CrisperWhisper)...")
    pipe, device = _get_crisper_pipe(device, progress=_progress)

    _progress(f"Transcribing {video.name} (verbatim mode)...")
    result = pipe(
        str(video),
        return_timestamps="word",
        chunk_length_s=chunk_length_s,
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

    words, audit = audit_words(words, video)
    # Group words into segments by pause gaps (>1s) or every ~30 words
    segments = _group_words_into_segments(words)
    json_path, srt_path, orig_srt_path = _write_transcript_outputs(
        segments, out_dir, stem, progress=_progress, audit=audit)
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
    parser.add_argument("--engine", default="mlx",
                        choices=["mlx", "crisperwhisper", "whisperx"],
                        help="Transcription engine (default: mlx)")

    args = parser.parse_args()
    transcribe(args.video, model=args.model, language=args.language,
               output_dir=args.output_dir, engine=args.engine)


if __name__ == "__main__":
    main()
