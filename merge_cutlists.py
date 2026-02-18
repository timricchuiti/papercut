#!/usr/bin/env python3
"""Merge transcript cuts into auto-editor command arguments."""

import subprocess
import sys


def merge_ranges(ranges):
    """Merge overlapping or adjacent time ranges into a minimal set.

    Args:
        ranges: List of (start, end) tuples in seconds.

    Returns:
        Sorted, non-overlapping list of (start, end) tuples.
    """
    if not ranges:
        return []

    sorted_ranges = sorted(ranges)
    merged = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))

    return merged


def build_auto_editor_cmd(video_path, transcript_cuts=None, margin=None,
                          export=None, extra_args=None):
    """Build an auto-editor command with transcript-based --cut-out ranges.

    Args:
        video_path: Path to the input video.
        transcript_cuts: List of (start, end) tuples in seconds.
        margin: Margin in seconds around cuts (passed to auto-editor).
        export: Export format (e.g., 'final-cut-pro', 'premiere', 'resolve').
        extra_args: Additional args to pass through to auto-editor.

    Returns:
        List of strings suitable for subprocess.run().
    """
    cmd = ["auto-editor", str(video_path)]

    if transcript_cuts:
        merged = merge_ranges(transcript_cuts)
        for start, end in merged:
            cmd.extend(["--cut-out", f"{start}s,{end}s"])

    if margin is not None:
        cmd.extend(["--margin", str(margin)])

    if export:
        cmd.extend(["--export", export])

    if extra_args:
        cmd.extend(extra_args)

    return cmd


def run_auto_editor(cmd):
    """Execute an auto-editor command.

    Args:
        cmd: Command list from build_auto_editor_cmd().

    Returns:
        subprocess.CompletedProcess result.
    """
    print(f"Running auto-editor...")
    print(f"  Command: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)

    if result.returncode != 0:
        print(f"Error: auto-editor failed (exit code {result.returncode})", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        sys.exit(1)

    print("auto-editor completed successfully.")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build and run auto-editor with cut ranges.")
    parser.add_argument("video", help="Path to the input video")
    parser.add_argument("--cuts", nargs="+", help="Cut ranges as start,end pairs (e.g., 5.2,7.4 30.1,35.8)")
    parser.add_argument("--margin", type=float, help="Margin in seconds")
    parser.add_argument("--export", help="Export format")
    parser.add_argument("--dry-run", action="store_true", help="Print command without running")
    args = parser.parse_args()

    cuts = []
    if args.cuts:
        for pair in args.cuts:
            start, end = pair.split(",")
            cuts.append((float(start), float(end)))

    cmd = build_auto_editor_cmd(args.video, cuts, args.margin, args.export)

    if args.dry_run:
        print(" ".join(cmd))
    else:
        run_auto_editor(cmd)
