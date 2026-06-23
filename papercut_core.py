#!/usr/bin/env python3
"""Shared export core for PaperCut.

Holds the logic that turns kept/edited transcript blocks into an output file
(FCPXML / Premiere XML / re-encoded media). Used by both the web GUI
(`web_gui.py`) and the headless batch CLI (`batch.py`) so there is exactly one
export path.

Pipeline:
    ordered_blocks (+ optional word-level edits)
      -> resolve_word_edits()      narrow edited blocks to kept word spans
      -> detect_silence()          amplitude-based silence detection
      -> build_clip_list()         intersect kept blocks with loud ranges
      -> generate_fcpxml() / generate_premiere_xml() / export_video()
"""

import re
from pathlib import Path

from transcript_diff import parse_srt, load_whisper_json
from silence import detect_silence, apply_margin, get_kept_ranges
from timeline_export import (
    build_clip_list, get_media_info,
    generate_fcpxml, generate_premiere_xml, export_video, validate_fcpxml,
)

# Map export format -> output file extension.
EXT_MAP = {
    "final-cut-pro": ".fcpxml",
    "resolve": ".fcpxml",
    "premiere": ".xml",
    "video": None,  # use source suffix
}

DEFAULT_THRESHOLD = 0.04


def normalize_word(w):
    """Normalize a word for comparison: lowercase, strip non-word chars."""
    return re.sub(r"[^\w]", "", w.lower())


def parse_threshold(edit_method, default=DEFAULT_THRESHOLD):
    """Extract a silence threshold from an edit_method string.

    e.g. "audio:threshold=0.04" -> 0.04. Returns `default` if absent/unparseable.
    """
    if edit_method:
        m = re.search(r"threshold=([0-9.]+)", edit_method)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return default


# Inline cut markers. Wrap the audio to REMOVE in these; everything else stays
# verbatim, so each word maps 1:1 to its timestamp (no fuzzy matching needed).
# Accepts a few spellings so hand-editing is forgiving.
CUT_OPEN = "[[CUT]]"
CUT_CLOSE = "[[/CUT]]"
_CUT_OPEN_RE = re.compile(r"\[\[\s*CUT\s*\]\]", re.IGNORECASE)
_CUT_CLOSE_RE = re.compile(r"\[\[\s*/\s*CUT\s*\]\]", re.IGNORECASE)


def has_cut_markers(text):
    return bool(_CUT_OPEN_RE.search(text or ""))


def tokenize_with_cuts(text):
    """Split text into (word, is_cut) tokens, honoring [[CUT]]..[[/CUT]] spans.

    Markers may be glued to words; they are isolated before splitting. An
    unclosed [[CUT]] cuts to the end of the block.
    """
    t = _CUT_OPEN_RE.sub(f" {CUT_OPEN} ", text)
    t = _CUT_CLOSE_RE.sub(f" {CUT_CLOSE} ", t)
    tokens, in_cut = [], False
    for tok in t.split():
        if tok == CUT_OPEN:
            in_cut = True
        elif tok == CUT_CLOSE:
            in_cut = False
        else:
            tokens.append((tok, in_cut))
    return tokens


def _merge_times_to_ranges(kept_word_times, margin):
    """Merge consecutive kept (start, end) word spans into contiguous ranges."""
    if not kept_word_times:
        return []
    ranges = [list(kept_word_times[0])]
    for start, end in kept_word_times[1:]:
        if start - ranges[-1][1] <= margin * 2:
            ranges[-1][1] = end
        else:
            ranges.append([start, end])
    return [{"start": r[0], "end": r[1]} for r in ranges]


def _match_rightmost(edited_words, block_words):
    """Map edited words to the LATEST matching source words (keep-last policy).

    Walks both sequences from the end so that when a fragment is repeated, the
    final (corrected) take is the one kept. Returns kept (start, end) spans in
    playback order. Words that don't match anything are skipped.
    """
    norm_block = [normalize_word(w["word"]) for w in block_words]
    kept_idx = []
    j = len(block_words) - 1
    for ew in reversed(edited_words):
        found = -1
        for i in range(j, -1, -1):
            if norm_block[i] == ew:
                found = i
                break
        if found >= 0:
            kept_idx.append(found)
            j = found - 1
    kept_idx.reverse()
    return [(block_words[i]["start"], block_words[i]["end"]) for i in kept_idx]


def resolve_word_edits(ordered_blocks, whisper_data, margin, warnings=None):
    """For edited blocks, use word-level timestamps to create sub-block ranges.

    Three cases per block:
      1. Not edited -> keep the whole block.
      2. Has [[CUT]] markers -> drop exactly the marked words (positional, exact)
         when the token count matches the block's words. If it does NOT match,
         a [[CUT]] cannot be placed precisely, so the block is kept WHOLE and a
         warning is appended to `warnings` (never a silent fuzzy cut that could
         remove the wrong words).
      3. Free-text edit (no markers) -> keep the surviving words, matched RIGHTMOST
         so that repeated takes resolve to the LAST occurrence.

    `warnings`: optional list; marker-placement failures are appended to it.

    Args:
        ordered_blocks: dicts with 'start', 'end', 'text', optional 'originalText'.
        whisper_data: WhisperX/CrisperWhisper JSON (word-level timestamps).
        margin: Seconds; kept words closer than 2*margin merge into one range.

    Returns:
        List of {"start", "end"} dicts in playback order.
    """
    if not whisper_data:
        return [{"start": b["start"], "end": b["end"]} for b in ordered_blocks]

    segments = whisper_data.get("segments", [])
    all_words = []
    for seg in segments:
        for w in seg.get("words", []):
            if "start" in w and "end" in w:
                all_words.append(w)

    resolved = []
    for block in ordered_blocks:
        text = block.get("text", "")
        original_text = block.get("originalText", "")
        marked = has_cut_markers(text)

        # (1) Untouched block -> keep whole.
        if not marked and (not original_text or text == original_text):
            resolved.append({"start": block["start"], "end": block["end"]})
            continue

        block_words = [
            w for w in all_words
            if w["start"] >= block["start"] - 0.05 and w["end"] <= block["end"] + 0.05
        ]
        if not block_words:
            resolved.append({"start": block["start"], "end": block["end"]})
            continue

        kept_word_times = None

        # (2) Exact positional cut via markers.
        if marked:
            toks = tokenize_with_cuts(text)
            word_toks = [t for t in toks if normalize_word(t[0])]
            if len(word_toks) == len(block_words):
                kept_word_times = [
                    (bw["start"], bw["end"])
                    for (tok, is_cut), bw in zip(word_toks, block_words)
                    if not is_cut
                ]
            else:
                # Marker tokens don't line up 1:1 with the audio's words (usually
                # an oddly-tokenized word: a glued stutter, an [UM], or a split
                # number). A [[CUT]] can't be placed exactly, and fuzzy-cutting
                # risks removing the WRONG words — so keep the block WHOLE and warn
                # so the edit gets fixed by hand instead of silently mis-cut.
                if warnings is not None:
                    mm = int(block["start"] // 60)
                    ss = block["start"] - mm * 60
                    snip = re.sub(r"\s+", " ", text).strip()
                    snip = (snip[:64] + "…") if len(snip) > 64 else snip
                    warnings.append(
                        f"[[CUT]] at {mm}:{ss:05.2f} not placed (token/word "
                        f"mismatch) — block kept WHOLE, fix by hand: \"{snip}\""
                    )
                resolved.append({"start": block["start"], "end": block["end"]})
                continue

        # (3) Rightmost free-text match (keep-last).
        if kept_word_times is None:
            edited_words = [normalize_word(w) for w in text.split() if normalize_word(w)]
            kept_word_times = _match_rightmost(edited_words, block_words)

        if not kept_word_times:
            # Nothing matched -> keep whole block rather than drop content.
            resolved.append({"start": block["start"], "end": block["end"]})
            continue

        resolved.extend(_merge_times_to_ranges(kept_word_times, margin))

    return resolved


def build_clips(video, ordered_blocks, whisper_data, margin=0.1,
                threshold=DEFAULT_THRESHOLD, media_info=None, warnings=None):
    """Resolve edits + silence-detect + build the final clip list.

    Returns (clips, media_info). Raises ValueError if no clips result.
    `warnings`: optional list for marker-placement failures (see resolve_word_edits).
    """
    video = Path(video)
    if media_info is None:
        media_info = get_media_info(str(video))
    frame_rate = media_info["frame_rate"]

    resolved_blocks = resolve_word_edits(ordered_blocks, whisper_data, margin,
                                         warnings=warnings)

    is_loud = detect_silence(str(video), threshold=threshold, frame_rate=frame_rate,
                             sample_rate=media_info["sample_rate"])
    apply_margin(is_loud, int(margin * frame_rate))
    kept_ranges = get_kept_ranges(is_loud, frame_rate)

    clips = build_clip_list(resolved_blocks, kept_ranges, margin=margin)
    if not clips:
        raise ValueError("No audio detected in kept blocks — nothing to export.")
    return clips, media_info


def write_export(video, clips, media_info, export_format, output_path,
                 ffmpeg_args=None):
    """Generate the chosen output format and write it to output_path."""
    video = str(video)
    output_path = str(output_path)

    if export_format in ("final-cut-pro", "resolve"):
        Path(output_path).write_text(
            generate_fcpxml(video, clips, media_info), encoding="utf-8")
    elif export_format == "premiere":
        Path(output_path).write_text(
            generate_premiere_xml(video, clips, media_info), encoding="utf-8")
    elif export_format == "video":
        extra = ffmpeg_args.split() if ffmpeg_args else None
        export_video(video, clips, output_path, extra_args=extra)
    else:
        raise ValueError(f"Unknown export format: {export_format}")
    return output_path


def resolve_output_path(video, export_format, export_folder=None, suffix="_ALTERED"):
    """Compute the output file path for a given video + format."""
    video = Path(video)
    ext = EXT_MAP.get(export_format)
    if ext is None:  # "video" -> keep source container
        ext = video.suffix
    out_dir = Path(export_folder).resolve() if export_folder else video.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / (video.stem + suffix + ext)


def export_from_blocks(video, ordered_blocks, whisper_data=None,
                       export_format="final-cut-pro", margin=0.1,
                       threshold=DEFAULT_THRESHOLD, edit_method="",
                       ffmpeg_args=None, output_path=None, export_folder=None):
    """High-level export from a list of ordered blocks (the GUI's data shape).

    Args:
        video: Path to source media.
        ordered_blocks: [{start, end, text, originalText}, ...] in playback order.
        whisper_data: Parsed WhisperX/CrisperWhisper JSON (or None).
        export_format: final-cut-pro | resolve | premiere | video.
        margin: Boundary padding in seconds.
        threshold: Silence amplitude threshold (overridden by edit_method if set).
        edit_method: e.g. "audio:threshold=0.04" — parsed for threshold.
        ffmpeg_args: Extra args for the "video" format.
        output_path: Explicit output path (else derived).
        export_folder: Directory for derived output (else next to source).

    Returns:
        dict with success, message, output_path, clip_count, total_duration.
    """
    video = Path(video).resolve()
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video}")
    if not ordered_blocks:
        raise ValueError("No blocks to export.")

    if edit_method:
        threshold = parse_threshold(edit_method, threshold)

    # Collect [[CUT]]-placement warnings while resolving edits.
    warnings = []
    clips, media_info = build_clips(video, ordered_blocks, whisper_data,
                                    margin=margin, threshold=threshold,
                                    warnings=warnings)

    if output_path is None:
        output_path = resolve_output_path(video, export_format, export_folder)

    write_export(video, clips, media_info, export_format, output_path,
                 ffmpeg_args=ffmpeg_args)

    # Guardrail: scan the written FCPXML for tiny clips / tiling errors.
    if export_format in ("final-cut-pro", "resolve"):
        try:
            warnings += validate_fcpxml(Path(output_path).read_text(encoding="utf-8"))
        except OSError:
            pass

    total_dur = sum(c.duration for c in clips)
    source_dur = media_info.get("duration", 0.0)
    return {
        "success": True,
        "output_path": str(output_path),
        "clip_count": len(clips),
        "total_duration": total_dur,
        "source_duration": source_dur,
        "warnings": warnings,
        "message": f"Export completed: {Path(output_path).name} "
                   f"({len(clips)} clips, {total_dur:.1f}s)",
    }


def _match_original_text(edited_block, orig_blocks, used, tol=0.5):
    """Find the original-SRT block matching an edited block (by start time).

    Returns the original text, or "" if no close match (treated as unedited).
    Marks the matched original index as used so duplicates map distinctly.
    """
    best_i, best_dist = None, None
    for i, ob in enumerate(orig_blocks):
        if i in used:
            continue
        dist = abs(ob.start - edited_block.start)
        if best_dist is None or dist < best_dist:
            best_dist, best_i = dist, i
    if best_i is not None and best_dist is not None and best_dist <= tol:
        used.add(best_i)
        return orig_blocks[best_i].text
    return ""


def srt_to_ordered_blocks(edited_srt, orig_srt=None):
    """Build GUI-style ordered_blocks from an edited SRT (+ original for diffing).

    Each block: {start, end, text, originalText}. When orig_srt is provided,
    originalText is filled from the time-aligned original block so within-block
    edits are detected; deleted blocks simply don't appear in the edited SRT.
    """
    edited_blocks = parse_srt(edited_srt)
    orig_blocks = parse_srt(orig_srt) if orig_srt and Path(orig_srt).exists() else []
    used = set()

    ordered = []
    for b in edited_blocks:
        original_text = _match_original_text(b, orig_blocks, used) if orig_blocks else ""
        ordered.append({
            "start": b.start,
            "end": b.end,
            "text": b.text,
            "originalText": original_text,
        })
    return ordered


def export_from_srt(video, edited_srt, whisper_json=None, orig_srt=None,
                    export_format="final-cut-pro", margin=0.1,
                    threshold=DEFAULT_THRESHOLD, ffmpeg_args=None,
                    output_path=None, export_folder=None):
    """Headless export: edited SRT (+ original + JSON) -> output file.

    This is the CLI twin of the GUI export. Deleted SRT blocks are dropped;
    blocks whose text differs from the original are narrowed to kept word spans
    via the WhisperX/CrisperWhisper JSON.

    Returns the same dict as export_from_blocks().
    """
    video = Path(video).resolve()
    edited_srt = Path(edited_srt)
    if not edited_srt.exists():
        raise FileNotFoundError(f"Edited SRT not found: {edited_srt}")

    # Default companion paths next to the edited SRT / video.
    if orig_srt is None:
        cand = Path(str(edited_srt) + ".orig")
        orig_srt = cand if cand.exists() else None
    if whisper_json is None:
        cand = video.with_suffix(".json")
        whisper_json = cand if cand.exists() else None

    whisper_data = load_whisper_json(str(whisper_json)) if whisper_json and Path(whisper_json).exists() else None

    ordered_blocks = srt_to_ordered_blocks(str(edited_srt),
                                           str(orig_srt) if orig_srt else None)
    if not ordered_blocks:
        raise ValueError(f"No usable blocks in {edited_srt}")

    return export_from_blocks(
        video, ordered_blocks, whisper_data=whisper_data,
        export_format=export_format, margin=margin, threshold=threshold,
        ffmpeg_args=ffmpeg_args, output_path=output_path,
        export_folder=export_folder,
    )
