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
    from auto_transcript import transcribe

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

    print(f"\nTranscribing {len(todo)} file(s) with engine={args.engine}...\n")
    ok, failed = 0, []
    for i, m in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {m.name}")
        try:
            transcribe(str(m), model=args.model, language=args.language,
                       output_dir=str(m.parent), engine=args.engine)
            ok += 1
        except SystemExit as e:  # auto_transcript calls sys.exit on hard errors
            failed.append((m.name, f"exit {e.code}"))
            print(f"  ERROR: transcription exited ({e.code})", file=sys.stderr)
        except Exception as e:
            failed.append((m.name, str(e)))
            print(f"  ERROR: {e}", file=sys.stderr)
            if args.verbose:
                traceback.print_exc()

    print(f"\nDone: {ok} ok, {len(failed)} failed.")
    for name, err in failed:
        print(f"  FAILED {name}: {err}", file=sys.stderr)
    return 0 if not failed else 2


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

            print(f"  -> {Path(result['output_path']).name}  "
                  f"{result['clip_count']} clips, kept {kept:.1f}s of {src:.1f}s "
                  f"({reduction*100:.0f}% removed)")
            if warn:
                print(f"  {warn}")

            report_lines.append(
                f"- **{m.name}** → `{Path(result['output_path']).name}` — "
                f"{result['clip_count']} clips, kept {kept:.1f}s / {src:.1f}s "
                f"({reduction*100:.0f}% removed)"
                + (f"  \n  {warn}" if warn else "")
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
    pt.add_argument("--engine", default="crisperwhisper",
                    choices=["crisperwhisper", "whisperx"],
                    help="Transcription engine (default: crisperwhisper)")
    pt.add_argument("--model", default="medium",
                    help="WhisperX model size (ignored for CrisperWhisper)")
    pt.add_argument("--language", default="en", help="Language code")
    pt.set_defaults(func=cmd_transcribe)

    px = sub.add_parser("export", parents=[common], help="Export edited SRTs")
    px.add_argument("--format", default="final-cut-pro",
                    choices=["final-cut-pro", "resolve", "premiere", "video"],
                    help="Export format (default: final-cut-pro)")
    px.add_argument("--margin", type=float, default=0.1,
                    help="Boundary padding in seconds (default: 0.1)")
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
