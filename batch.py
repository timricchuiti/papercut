#!/usr/bin/env python3
"""PaperCut batch driver — headless transcribe / export over a directory.

Designed to be run by Claude (or a human) to process 5–10 raw videos at a time:

    # 1. Transcribe everything in a folder (verbatim, fillers + repeats):
    .venv-crisper/bin/python batch.py transcribe /path/to/videos

    # 2. (Claude edits each <stem>.srt — delete flubs, keep last of repeats,
    #     log uncertain calls to <stem>.notes.md)

    # 3. Produce FCP XMLs next to each source file:
    .venv-crisper/bin/python batch.py export /path/to/videos

    # See state at any time:
    .venv-crisper/bin/python batch.py status /path/to/videos

Run under the CrisperWhisper venv (`.venv-crisper/bin/python`) so transcription
and the numpy-based export both have their dependencies.
"""

import argparse
import subprocess
import sys
import traceback
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aiff", ".aif", ".ogg"}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS

ALTERED_SUFFIX = "_ALTERED"

# Over/under-cut heuristics (fraction of source removed).
OVERCUT_FRACTION = 0.60   # >60% removed -> likely too aggressive
UNDERCUT_FRACTION = 0.02  # <2% removed -> probably no real editing happened


def find_media(directory):
    """Return sorted media files in a directory, excluding _ALTERED outputs."""
    directory = Path(directory)
    files = []
    for p in sorted(directory.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() not in MEDIA_EXTS:
            continue
        if p.stem.endswith(ALTERED_SUFFIX):
            continue
        files.append(p)
    return files


def companion_paths(media):
    """Return (json, srt, orig_srt) paths for a media file."""
    media = Path(media)
    return (
        media.with_suffix(".json"),
        media.with_suffix(".srt"),
        Path(str(media.with_suffix(".srt")) + ".orig"),
    )


def file_state(media):
    """Classify a media file's pipeline state.

    Returns dict: {transcribed, edited, exported, output}.
    """
    json_p, srt_p, orig_p = companion_paths(media)
    transcribed = json_p.exists() and srt_p.exists() and orig_p.exists()

    edited = False
    if transcribed:
        try:
            edited = srt_p.read_text(encoding="utf-8") != orig_p.read_text(encoding="utf-8")
        except OSError:
            edited = False

    # Any _ALTERED.* output next to the source counts as exported.
    output = None
    for ext in (".fcpxml", ".xml", media.suffix):
        cand = media.with_name(media.stem + ALTERED_SUFFIX + ext)
        if cand.exists():
            output = cand
            break

    return {
        "transcribed": transcribed,
        "edited": edited,
        "exported": output is not None,
        "output": output,
    }


# ---------------------------------------------------------------------------
# transcribe
# ---------------------------------------------------------------------------

def cmd_transcribe(args):
    media = find_media(args.directory)
    if not media:
        print(f"No media files found in {args.directory}", file=sys.stderr)
        return 1

    todo = []
    for m in media:
        st = file_state(m)
        if st["transcribed"] and not args.force:
            print(f"  skip (already transcribed): {m.name}")
            continue
        todo.append(m)

    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        print("Nothing to transcribe.")
        return 0

    # Two transcription modes:
    #
    # Subprocess-per-file (default): each file runs in its own process, torn down
    # on exit so the OS reclaims all memory between files. Essential on a
    # RAM-constrained machine (model + intermediates accumulating in one process
    # is what swap-thrashed the old 16GB box), and it isolates the deterministic
    # CrisperWhisper crash to a single file. Cost: every file reloads the model
    # AND re-compiles GPU kernels from cold, re-paying the ~3.5x warmup tax.
    #
    # In-process (--in-process): all files run in one process, so the model loads
    # once and the warm GPU kernels are reused — files 2..N run at the ~1.7x warm
    # rate. Worth it when RAM is ample (the model is ~3GB). Trade-off: a hard
    # crash takes down the whole run, and memory isn't reclaimed between files.
    if getattr(args, "in_process", False):
        return _transcribe_in_process(todo, args)

    auto_script = str(Path(__file__).resolve().parent / "auto_transcript.py")

    def _subprocess_one(m):
        cmd = [
            sys.executable, auto_script, str(m),
            "--engine", args.engine,
            "--language", args.language,
            "--model", args.model,
            "--output-dir", str(m.parent),
        ]
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            raise RuntimeError(f"transcription subprocess failed (exit {rc})")

    return _run_batch(todo, f"engine={args.engine}, isolated subprocess per file",
                      _subprocess_one, verbose=args.verbose)


def _run_batch(todo, label, do_one, verbose=False):
    """Run do_one(m) over todo with shared progress + failure accounting.

    do_one(m) transcribes one file and raises on failure; a file counts as ok only
    if it didn't raise AND a transcript landed on disk.
    """
    print(f"\nTranscribing {len(todo)} file(s) ({label})...\n")
    ok, failed = 0, []
    for i, m in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {m.name}", flush=True)
        try:
            do_one(m)
        except Exception as e:
            failed.append((m.name, str(e)))
            print(f"  ERROR: {e}", file=sys.stderr)
            if verbose:
                import traceback
                traceback.print_exc()
            continue
        if file_state(m)["transcribed"]:           # trust the on-disk result
            ok += 1
        else:
            failed.append((m.name, "no transcript written"))
            print(f"  ERROR: no transcript written for {m.name}", file=sys.stderr)

    print(f"\nDone: {ok} ok, {len(failed)} failed.")
    for name, err in failed:
        print(f"  FAILED {name}: {err}", file=sys.stderr)
    return 0 if not failed else 2


def _transcribe_in_process(todo, args):
    """Transcribe all files in one process so the model/kernels stay warm.

    Only helps the `crisperwhisper` engine (auto_transcript caches its pipeline
    per-process, so files 2..N skip the load + GPU-warmup). `mlx` re-spawns a
    .venv-mlx subprocess per file regardless, and `whisperx` is a CLI call — for
    those this is equivalent to the default mode. A hard crash ends the whole run
    (use the default subprocess mode for per-file isolation).
    """
    from auto_transcript import transcribe

    def _in_process_one(m):
        transcribe(str(m), engine=args.engine, language=args.language,
                   model=args.model, output_dir=str(m.parent))

    return _run_batch(todo, f"engine={args.engine}, in-process (model stays warm)",
                      _in_process_one, verbose=args.verbose)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def _cut_warning(reduction):
    if reduction >= OVERCUT_FRACTION:
        return f"⚠ OVER-CUT? {reduction*100:.0f}% of source removed — review for missing content."
    if reduction <= UNDERCUT_FRACTION:
        return f"⚠ Almost nothing cut ({reduction*100:.1f}%) — was this transcript edited?"
    return None


def cmd_export(args):
    from papercut_core import export_from_srt

    media = find_media(args.directory)
    if not media:
        print(f"No media files found in {args.directory}", file=sys.stderr)
        return 1

    todo = []
    for m in media:
        st = file_state(m)
        if not st["transcribed"]:
            print(f"  skip (not transcribed): {m.name}")
            continue
        if st["exported"] and not args.force:
            print(f"  skip (already exported): {m.name}")
            continue
        todo.append(m)

    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        print("Nothing to export.")
        return 0

    print(f"\nExporting {len(todo)} file(s) as {args.format}...\n")
    report_lines = ["# PaperCut batch export report", ""]
    ok, failed = 0, []
    for i, m in enumerate(todo, 1):
        json_p, srt_p, orig_p = companion_paths(m)
        print(f"[{i}/{len(todo)}] {m.name}")
        try:
            result = export_from_srt(
                str(m), str(srt_p),
                whisper_json=str(json_p) if json_p.exists() else None,
                orig_srt=str(orig_p) if orig_p.exists() else None,
                export_format=args.format, margin=args.margin,
                threshold=args.threshold,
            )
            src = result.get("source_duration") or 0.0
            kept = result.get("total_duration") or 0.0
            reduction = (1 - kept / src) if src > 0 else 0.0
            warn = _cut_warning(reduction)
            checks = result.get("warnings", [])  # tiny-clip / tiling guardrail

            print(f"  -> {Path(result['output_path']).name}  "
                  f"{result['clip_count']} clips, kept {kept:.1f}s of {src:.1f}s "
                  f"({reduction*100:.0f}% removed)")
            if warn:
                print(f"  {warn}")
            for c in checks:
                print(f"  ⚠ OUTPUT CHECK: {c}", file=sys.stderr)

            report_lines.append(
                f"- **{m.name}** → `{Path(result['output_path']).name}` — "
                f"{result['clip_count']} clips, kept {kept:.1f}s / {src:.1f}s "
                f"({reduction*100:.0f}% removed)"
                + (f"  \n  {warn}" if warn else "")
                + "".join(f"  \n  ⚠ OUTPUT CHECK: {c}" for c in checks)
            )
            ok += 1
        except Exception as e:
            failed.append((m.name, str(e)))
            print(f"  ERROR: {e}", file=sys.stderr)
            if args.verbose:
                traceback.print_exc()
            report_lines.append(f"- **{m.name}** — ❌ FAILED: {e}")

    report_path = Path(args.directory) / "papercut_batch_report.md"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(f"\nDone: {ok} ok, {len(failed)} failed. Report: {report_path}")
    for name, err in failed:
        print(f"  FAILED {name}: {err}", file=sys.stderr)
    return 0 if not failed else 2


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args):
    media = find_media(args.directory)
    if not media:
        print(f"No media files found in {args.directory}")
        return 0

    def mark(b):
        return "✓" if b else "·"

    print(f"\n{'transcribed':>11} {'edited':>6} {'exported':>8}   file")
    print("-" * 60)
    for m in media:
        st = file_state(m)
        print(f"{mark(st['transcribed']):>11} {mark(st['edited']):>6} "
              f"{mark(st['exported']):>8}   {m.name}")
    print()
    n = len(media)
    nt = sum(file_state(m)["transcribed"] for m in media)
    ne = sum(file_state(m)["edited"] for m in media)
    nx = sum(file_state(m)["exported"] for m in media)
    print(f"{n} media · {nt} transcribed · {ne} edited · {nx} exported")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="PaperCut batch driver.")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("directory", help="Directory of media files")
    common.add_argument("--limit", type=int, default=None,
                        help="Process at most N files this run")
    common.add_argument("--force", action="store_true",
                        help="Reprocess even if outputs already exist")
    common.add_argument("--verbose", action="store_true",
                        help="Print full tracebacks on error")

    pt = sub.add_parser("transcribe", parents=[common], help="Transcribe media")
    pt.add_argument("--engine", default="mlx",
                    choices=["mlx", "crisperwhisper", "whisperx"],
                    help="Transcription engine (default: mlx)")
    pt.add_argument("--model", default="medium",
                    help="WhisperX model size (ignored for mlx/CrisperWhisper)")
    pt.add_argument("--language", default="en", help="Language code")
    pt.add_argument("--in-process", action="store_true",
                    help="Transcribe all files in one process so the model stays "
                         "warm (crisperwhisper only; no effect for mlx/whisperx). "
                         "Default: subprocess per file.")
    pt.set_defaults(func=cmd_transcribe)

    px = sub.add_parser("export", parents=[common], help="Export edited SRTs")
    px.add_argument("--format", default="final-cut-pro",
                    choices=["final-cut-pro", "resolve", "premiere", "video"],
                    help="Export format (default: final-cut-pro)")
    px.add_argument("--margin", type=float, default=0.0,
                    help="Edge tightness in seconds: 0 sits at the speech "
                         "(default), >0 adds breath, <0 (e.g. -0.1) cuts tighter")
    px.add_argument("--threshold", type=float, default=0.04,
                    help="Silence amplitude threshold (default: 0.04)")
    px.set_defaults(func=cmd_export)

    ps = sub.add_parser("status", parents=[common], help="Show pipeline state")
    ps.set_defaults(func=cmd_status)

    return p


def main():
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
