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

import bisect
import difflib
import re
from pathlib import Path

from transcript_diff import parse_srt, load_whisper_json
from silence import detect_silence, apply_margin, bridge_gaps, get_kept_ranges
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

# Default edge margin (seconds): a little breath around each clip. 0 sits right at
# the detected speech; negative erodes into it. 0.07 is Tim's tuned preference
# (0 = too tight, 0.10 doubled = the old dead-air bug).
DEFAULT_MARGIN = 0.07

# Silent gaps shorter than this (seconds) are bridged, not cut — they're the natural
# micro-pauses inside speech. Cutting them would fragment a sentence into dozens of
# clips and sound choppy. Longer gaps are real pauses and get cut. Decoupled from
# `margin` (which only controls how tight the kept edges are), mirroring auto-editor.
SILENCE_BRIDGE_S = 0.20

# Edge snap (seconds): MLX/CrisperWhisper word timestamps can END early (clipping a
# word's tail: "ninety thousan-") or START late (clipping an onset: "-urthermore").
# After silence detection, a kept edge that still sits inside loud audio is pushed
# outward to the loud range's edge — but never past the neighbouring source word
# (which may be a cut take) and never more than this many seconds. 0 disables.
EDGE_SNAP_S = 0.5

# Words shorter than this (seconds) are zero-width "ghost" tokens from an MLX
# decoding loop, not real speech; they are ignored when looking up neighbours.
GHOST_WORD_S = 0.02

# [[FLAG: note]] annotations in a block's text become FCPXML markers at that block's
# start time (visible on the FCP timeline), and are stripped before word matching.
FLAG_RE = re.compile(r"\[\[FLAG:?\s*(.*?)\s*\]\]", re.DOTALL)


def extract_flags(text):
    """Strip [[FLAG: note]] annotations from text; return (clean_text, [notes])."""
    notes = [m.group(1) for m in FLAG_RE.finditer(text)]
    if not notes:
        return text, []
    clean = FLAG_RE.sub(" ", text)
    clean = re.sub(r"[ \t]{2,}", " ", clean).strip()
    return clean, notes


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
    """True if the text carries an opening OR closing cut marker.

    A lone [[/CUT]] matters too: it closes a span that was opened in an earlier
    block (cross-block cuts — see resolve_word_edits).
    """
    text = text or ""
    return bool(_CUT_OPEN_RE.search(text) or _CUT_CLOSE_RE.search(text))


def tokenize_with_cuts(text, in_cut=False):
    """Split text into (word, is_cut) tokens, honoring [[CUT]]..[[/CUT]] spans.

    Markers may be glued to words; they are isolated before splitting. `in_cut`
    is the state carried in from the previous block: an unclosed [[CUT]] cuts to
    the end of the block AND into the following blocks until a [[/CUT]] appears,
    so a re-take that straddles an SRT block boundary needs no manual block merge.
    Returns (tokens, in_cut_at_end).
    """
    t = _CUT_OPEN_RE.sub(f" {CUT_OPEN} ", text)
    t = _CUT_CLOSE_RE.sub(f" {CUT_CLOSE} ", t)
    tokens = []
    for tok in t.split():
        if tok == CUT_OPEN:
            in_cut = True
        elif tok == CUT_CLOSE:
            in_cut = False
        else:
            tokens.append((tok, in_cut))
    return tokens, in_cut


def _align_tokens(word_toks, block_words):
    """Align text tokens to source words by text when the counts don't match.

    Returns one block_words index per token, or None if any token has no match.
    Used when the time-window word pick-up for a block grabbed extra words — a
    zero-width ghost loop sitting exactly on the block boundary gets counted into
    both neighbouring blocks — so a 1:1 positional zip is impossible. Only an
    alignment that places EVERY text token is accepted; otherwise the caller
    keeps the block whole and warns (never a fuzzy cut of the wrong words).
    """
    a = [normalize_word(t) for t, _ in word_toks]
    b = [normalize_word(w["word"]) for w in block_words]
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    idx = [None] * len(a)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                idx[i1 + k] = j1 + k
    if any(i is None for i in idx):
        return None
    return idx


def _merge_times_to_ranges(kept_word_times, max_gap):
    """Merge consecutive kept (start, end) word spans into contiguous ranges.

    Words separated by a gap <= max_gap seconds become one range (so a kept run of
    speech isn't split into per-word ranges); a larger gap starts a new range.
    """
    if not kept_word_times:
        return []
    ranges = [list(kept_word_times[0])]
    for start, end in kept_word_times[1:]:
        if start - ranges[-1][1] <= max_gap:
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


def resolve_word_edits(ordered_blocks, whisper_data, max_gap, warnings=None):
    """For edited blocks, use word-level timestamps to create sub-block ranges.

    Three cases per block:
      1. Not edited -> keep the whole block.
      2. Has [[CUT]] markers -> drop exactly the marked words. Positional when the
         token count matches the block's words; otherwise aligned by text (the
         usual mismatch is a zero-width ghost loop on the block boundary that got
         picked up by both neighbours). If a [[CUT]] still cannot be placed, the
         block is kept WHOLE and a warning is appended to `warnings` (never a
         silent fuzzy cut that could remove the wrong words).
         An unclosed [[CUT]] carries into the following blocks until a [[/CUT]]
         closes it, so a re-take spanning two SRT blocks is one cut.
      3. Free-text edit (no markers) -> keep the surviving words, matched RIGHTMOST
         so that repeated takes resolve to the LAST occurrence.

    `warnings`: optional list; marker-placement failures are appended to it.

    Args:
        ordered_blocks: dicts with 'start', 'end', 'text', optional 'originalText'.
        whisper_data: WhisperX/CrisperWhisper JSON (word-level timestamps).
        max_gap: Seconds; kept words closer than this merge into one range.

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

    def warn(block, text, why):
        if warnings is None:
            return
        mm = int(block["start"] // 60)
        ss = block["start"] - mm * 60
        snip = re.sub(r"\s+", " ", text).strip()
        snip = (snip[:64] + "…") if len(snip) > 64 else snip
        warnings.append(f"[[CUT]] at {mm}:{ss:05.2f} not placed ({why}) — "
                        f"block kept WHOLE, fix by hand: \"{snip}\"")

    resolved = []
    in_cut = False  # cut-span state carried across blocks
    for block in ordered_blocks:
        text = block.get("text", "")
        original_text = block.get("originalText", "")
        marked = has_cut_markers(text) or in_cut

        # (1) Untouched block -> keep whole.
        if not marked and (not original_text or text == original_text):
            resolved.append({"start": block["start"], "end": block["end"]})
            continue

        block_words = [
            w for w in all_words
            if w["start"] >= block["start"] - 0.05 and w["end"] <= block["end"] + 0.05
        ]

        kept_word_times = None

        # (2) Exact cut via markers (positional, else text-aligned).
        if marked:
            toks, in_cut = tokenize_with_cuts(text, in_cut)
            word_toks = [t for t in toks if normalize_word(t[0])]
            if word_toks and all(is_cut for _, is_cut in word_toks):
                # Every word is inside a cut span (explicitly, or the block sits
                # wholly inside a carried-over span): drop the block.
                continue
            if not block_words or not word_toks:
                resolved.append({"start": block["start"], "end": block["end"]})
                continue
            if len(word_toks) == len(block_words):
                pairs = zip(word_toks, block_words)
            else:
                idx = _align_tokens(word_toks, block_words)
                if idx is None:
                    warn(block, text, "token/word mismatch")
                    resolved.append({"start": block["start"], "end": block["end"]})
                    continue
                pairs = ((tok, block_words[i]) for tok, i in zip(word_toks, idx))
            kept_word_times = [(bw["start"], bw["end"])
                               for (tok, is_cut), bw in pairs if not is_cut]
            if not kept_word_times:
                continue  # all matched words cut -> intentional whole-block cut
        elif not block_words:
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

        resolved.extend(_merge_times_to_ranges(kept_word_times, max_gap))

    return resolved


def snap_to_speech(ranges, loud_ranges, whisper_data, max_snap=EDGE_SNAP_S):
    """Push kept-range edges outward to the loud edge when audio is still sounding.

    Word timestamps are the cut positions, but MLX word ENDS can be early and
    STARTS late by a few hundred ms, so intersecting with the loud ranges alone
    clips word tails/onsets wherever a kept edge is followed/preceded by silence.
    For each range edge that falls inside a loud range, extend it to that loud
    range's edge (which already carries the user's margin), bounded by:
      - the next/previous real source word (so a cut take right next door is
        never pulled back in — the edge stops at that word's boundary), and
      - `max_snap` seconds.
    Edges already in silence are left alone. Zero-width ghost words are ignored.
    Returns a new list of {"start","end"} ranges (same order).
    """
    if not ranges or max_snap <= 0 or not loud_ranges or not whisper_data:
        return ranges
    words = sorted(
        ((w["start"], w["end"]) for seg in whisper_data.get("segments", [])
         for w in seg.get("words", [])
         if "start" in w and "end" in w and w["end"] - w["start"] >= GHOST_WORD_S),
    )
    starts = [a for a, _ in words]
    ends = sorted(b for _, b in words)
    loud = sorted(loud_ranges)
    loud_starts = [a for a, _ in loud]

    def loud_at(t):
        i = bisect.bisect_right(loud_starts, t) - 1
        if i >= 0 and loud[i][0] <= t < loud[i][1]:
            return loud[i]
        return None

    out = []
    for r in ranges:
        s, e = r["start"], r["end"]
        L = loud_at(e - 1e-6)
        if L and L[1] > e:
            # Next word = first word starting at/after this edge (a word that
            # abuts the edge exactly counts — it is the cut take's first word).
            j = bisect.bisect_left(starts, e - 0.01)
            nxt = starts[j] if j < len(starts) else float("inf")
            e = max(e, min(L[1], nxt, e + max_snap))
        L = loud_at(s)
        if L and L[0] < s:
            j = bisect.bisect_right(ends, s + 0.01) - 1
            prv = ends[j] if j >= 0 else float("-inf")
            s = min(s, max(L[0], prv, s - max_snap))
        out.append({"start": s, "end": e})
    return out


def build_clips(video, ordered_blocks, whisper_data, margin=DEFAULT_MARGIN,
                threshold=DEFAULT_THRESHOLD, bridge=SILENCE_BRIDGE_S,
                media_info=None, warnings=None, snap=EDGE_SNAP_S):
    """Resolve edits + silence-detect + build the final clip list.

    `margin` (seconds) controls only how tight the kept edges are: > 0 pads each clip
    with a little silence (breath), < 0 (auto-editor's -0.1) bites into the speech for
    tighter cuts, 0 sits right at the speech. `bridge` (seconds) is the separate
    defragmentation knob: silent gaps up to ~2*bridge inside speech are kept (natural
    micro-pauses) instead of shattering a sentence into dozens of clips.

    Returns (clips, media_info). Raises ValueError if no clips result.
    `snap` (seconds) caps how far a kept edge may move outward to the loud edge
    (see snap_to_speech); 0 disables snapping.
    `warnings`: optional list for marker-placement failures (see resolve_word_edits).
    """
    video = Path(video)
    if media_info is None:
        media_info = get_media_info(str(video))
    frame_rate = media_info["frame_rate"]

    resolved_blocks = resolve_word_edits(ordered_blocks, whisper_data, bridge,
                                         warnings=warnings)

    is_loud = detect_silence(str(video), threshold=threshold, frame_rate=frame_rate,
                             sample_rate=media_info["sample_rate"])
    # 1. Bridge micro-pauses so a run of speech stays one clip (no fragmenting).
    bridge_gaps(is_loud, round(bridge * frame_rate))
    # 2. Margin: pad (>0) or erode (<0) the kept edges — the only role margin plays.
    apply_margin(is_loud, round(margin * frame_rate))
    kept_ranges = get_kept_ranges(is_loud, frame_rate)
    # 3. Snap: a kept edge still inside loud audio moves out to the loud edge
    #    (bounded by the neighbouring word) so early word-ends / late word-starts
    #    from the transcriber don't clip tails and onsets.
    resolved_blocks = snap_to_speech(resolved_blocks, kept_ranges, whisper_data,
                                     max_snap=snap)

    clips = build_clip_list(resolved_blocks, kept_ranges)
    if not clips:
        raise ValueError("No audio detected in kept blocks — nothing to export.")
    return clips, media_info


def write_export(video, clips, media_info, export_format, output_path,
                 ffmpeg_args=None, flags=None):
    """Generate the chosen output format and write it to output_path.

    `flags`: optional [{"time": sec, "note": str}] — embedded as timeline markers
    in FCPXML formats (ignored for premiere/video).
    """
    video = str(video)
    output_path = str(output_path)

    # The FCP/Premiere project is named after the OUTPUT file, so several cuts of
    # one source (foo_ALTERED, foo_SHORT_ALTERED) import as distinct projects.
    project_name = Path(output_path).stem
    if export_format in ("final-cut-pro", "resolve"):
        Path(output_path).write_text(
            generate_fcpxml(video, clips, media_info, flags=flags,
                            project_name=project_name),
            encoding="utf-8")
    elif export_format == "premiere":
        Path(output_path).write_text(
            generate_premiere_xml(video, clips, media_info,
                                  project_name=project_name), encoding="utf-8")
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
                       export_format="final-cut-pro", margin=DEFAULT_MARGIN,
                       threshold=DEFAULT_THRESHOLD, bridge=SILENCE_BRIDGE_S,
                       edit_method="", ffmpeg_args=None, output_path=None,
                       export_folder=None, snap=EDGE_SNAP_S):
    """High-level export from a list of ordered blocks (the GUI's data shape).

    Args:
        video: Path to source media.
        ordered_blocks: [{start, end, text, originalText}, ...] in playback order.
        whisper_data: Parsed WhisperX/CrisperWhisper JSON (or None).
        export_format: final-cut-pro | resolve | premiere | video.
        margin: Edge tightness in seconds (>0 breath, <0 tighter, 0 at the speech).
        threshold: Silence amplitude threshold (overridden by edit_method if set).
        bridge: Keep silent gaps up to ~2*bridge seconds inside speech (defrag knob).
        snap: Max seconds a kept edge may extend to the loud edge (0 = off).
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

    # Pull [[FLAG: note]] annotations out of the block texts BEFORE word matching
    # (they'd otherwise break token alignment). Each becomes an FCPXML marker at
    # the block's start time.
    flags = []
    for block in ordered_blocks:
        clean, notes = extract_flags(block.get("text", ""))
        if notes:
            block["text"] = clean
            flags.extend({"time": block["start"], "note": n} for n in notes)
        if block.get("originalText"):
            block["originalText"] = extract_flags(block["originalText"])[0]

    # Collect [[CUT]]-placement warnings while resolving edits.
    warnings = []
    clips, media_info = build_clips(video, ordered_blocks, whisper_data,
                                    margin=margin, threshold=threshold,
                                    bridge=bridge, warnings=warnings, snap=snap)

    if output_path is None:
        output_path = resolve_output_path(video, export_format, export_folder)

    write_export(video, clips, media_info, export_format, output_path,
                 ffmpeg_args=ffmpeg_args, flags=flags)

    # Guardrail: scan the written FCPXML for tiny clips / tiling errors.
    if export_format in ("final-cut-pro", "resolve"):
        try:
            warnings += validate_fcpxml(Path(output_path).read_text(encoding="utf-8"))
        except OSError:
            pass

    total_dur = sum(c.duration for c in clips)
    source_dur = media_info.get("duration", 0.0)
    marker_note = f", {len(flags)} flag marker(s)" if flags else ""
    return {
        "success": True,
        "output_path": str(output_path),
        "clip_count": len(clips),
        "total_duration": total_dur,
        "source_duration": source_dur,
        "flag_count": len(flags),
        "warnings": warnings,
        "message": f"Export completed: {Path(output_path).name} "
                   f"({len(clips)} clips, {total_dur:.1f}s{marker_note})",
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
                    export_format="final-cut-pro", margin=DEFAULT_MARGIN,
                    threshold=DEFAULT_THRESHOLD, bridge=SILENCE_BRIDGE_S,
                    ffmpeg_args=None, output_path=None, export_folder=None,
                    snap=EDGE_SNAP_S):
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
        bridge=bridge, ffmpeg_args=ffmpeg_args, output_path=output_path,
        export_folder=export_folder, snap=snap,
    )
