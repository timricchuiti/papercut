#!/usr/bin/env python3
"""Timeline export — FCPXML, Premiere XML, and ffmpeg video export.

Replaces auto-editor's export functionality with support for reordered clips.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape


@dataclass
class Clip:
    """A single clip referencing a region of the source media."""
    source_in: float   # seconds — start in source
    source_out: float  # seconds — end in source

    @property
    def duration(self):
        return self.source_out - self.source_in


def get_media_info(path):
    """Probe media file with ffprobe and return metadata dict.

    Returns:
        {
            "duration": float,
            "frame_rate": float,
            "frame_rate_num": int,
            "frame_rate_den": int,
            "width": int | None,
            "height": int | None,
            "sample_rate": int,
            "has_video": bool,
            "has_audio": bool,
        }
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries",
        "stream=codec_type,width,height,r_frame_rate,sample_rate,"
        "pix_fmt,color_space,color_primaries,color_transfer",
        "-print_format", "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr[:500]}")

    data = json.loads(result.stdout)

    info = {
        "duration": float(data.get("format", {}).get("duration", 0)),
        "frame_rate": 30.0,
        "frame_rate_num": 30,
        "frame_rate_den": 1,
        "width": None,
        "height": None,
        "sample_rate": 48000,
        "has_video": False,
        "has_audio": False,
        "color_space": "1-1-1 (Rec. 709)",
    }

    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "video":
            info["has_video"] = True
            if stream.get("width"):
                info["width"] = int(stream["width"])
            if stream.get("height"):
                info["height"] = int(stream["height"])
            rfr = stream.get("r_frame_rate", "30/1")
            if "/" in rfr:
                num, den = rfr.split("/")
                num, den = int(num), int(den)
                if den > 0:
                    info["frame_rate"] = num / den
                    info["frame_rate_num"] = num
                    info["frame_rate_den"] = den
            info["color_space"] = _fcp_colorspace(stream)
        elif codec_type == "audio":
            info["has_audio"] = True
            if stream.get("sample_rate"):
                info["sample_rate"] = int(stream["sample_rate"])

    return info


def _fcp_colorspace(stream):
    """Map ffprobe color metadata to an FCPXML colorSpace string.

    Mirrors auto-editor's mapping. Defaults to Rec. 709, which is what FCP
    expects for typical SDR H.264/HEVC screen recordings.
    """
    pix_fmt = stream.get("pix_fmt", "")
    cs = stream.get("color_space", "")
    cp = stream.get("color_primaries", "")
    ct = stream.get("color_transfer", "")

    if pix_fmt == "rgb24":
        return "sRGB IEC61966-2.1"
    if cs == "bt470bg":
        return "5-1-6 (Rec. 601 PAL)"
    if cs == "smpte170m":
        return "6-1-6 (Rec. 601 NTSC)"
    if cp == "bt2020":
        if ct in ("smpte2084", "arib-std-b67"):
            return "9-18-9 (Rec. 2020 HLG)"
        return "9-1-9 (Rec. 2020)"
    return "1-1-1 (Rec. 709)"


#: Clips shorter than this (seconds) are dropped — they're boundary slivers
#: from a transient crossing the silence threshold for a frame or two, not real
#: content. Smallest legitimate speech clips run ~0.2s+, so this is safe.
MIN_CLIP_DURATION = 0.1


def build_clip_list(ordered_blocks, silence_kept_ranges, margin=0.0,
                    min_clip_dur=MIN_CLIP_DURATION):
    """Build final clip list from user's ordered blocks + silence-detected kept ranges.

    For each kept block (in user's order), find silence-detected kept ranges that
    overlap with the block's time span, clip them to block boundaries, and apply margin.

    Args:
        ordered_blocks: List of dicts with 'start' and 'end' (seconds), in user's desired order.
        silence_kept_ranges: List of (start_sec, end_sec) from silence detection.
        margin: Extra padding in seconds to add around each clip boundary.
        min_clip_dur: Drop clips shorter than this many seconds (sliver filter).

    Returns:
        List of Clip objects in playback order.
    """
    clips = []

    for block in ordered_blocks:
        block_start = block["start"]
        block_end = block["end"]

        # Find silence-kept ranges overlapping this block
        # The kept_ranges already have margin applied (from apply_margin in silence.py),
        # so we just need to find which ones overlap with this block's time span.
        # We allow kept ranges to extend slightly beyond block boundaries — the margin
        # is meant to include a bit of silence before/after speech.
        block_clips = []
        for rng_start, rng_end in silence_kept_ranges:
            # Check if this kept range overlaps the block (with margin tolerance)
            overlap_start = max(rng_start, block_start - margin)
            overlap_end = min(rng_end, block_end + margin)
            if overlap_start < overlap_end:
                block_clips.append(Clip(source_in=overlap_start, source_out=overlap_end))

        if block_clips:
            # Merge overlapping clips within this block
            merged = [block_clips[0]]
            for c in block_clips[1:]:
                if c.source_in <= merged[-1].source_out:
                    merged[-1] = Clip(merged[-1].source_in, max(merged[-1].source_out, c.source_out))
                else:
                    merged.append(c)
            clips.extend(merged)
        else:
            # No silence data overlaps — keep entire block
            clips.append(Clip(source_in=block_start, source_out=block_end))

    # Drop sub-threshold slivers (transients clipped at block boundaries).
    if min_clip_dur > 0:
        clips = [c for c in clips if c.duration >= min_clip_dur]

    return clips


def _fcp_format_name(width, height, fr_num, fr_den):
    """Return an FCP-recognized format name, or None for non-standard formats.

    FCP only recognizes a small set of predefined format names. Using a name FCP
    doesn't know — or the "FFVideoFormatRateUndefined" token alongside a concrete
    frameDuration — triggers an "unexpected value" import warning. For anything
    outside the known set (e.g. 1080p60, 4K, portrait, odd rates), return None so
    the caller emits a NAMELESS format defined purely by frameDuration/width/
    height, which FCP accepts for any resolution and frame rate.
    """
    fps = round(fr_num / fr_den)
    scan_lines = min(width, height)
    if scan_lines == 720 and fps == 30:
        return "FFVideoFormat720p30"
    if scan_lines == 720 and fps == 25:
        return "FFVideoFormat720p25"
    if (width, height) == (3840, 2160) and (fr_num, fr_den) == (24000, 1001):
        return "FFVideoFormat3840x2160p2398"
    # Non-standard (e.g. 1080p60): FCP infers from frameDuration/width/height/
    # colorSpace. "RateUndefined" + those attributes is what auto-editor emits.
    return "FFVideoFormatRateUndefined"


def generate_fcpxml(media_path, clips, media_info):
    """Generate FCPXML 1.11 string for Final Cut Pro / DaVinci Resolve.

    Args:
        media_path: Path to the source media file.
        clips: List of Clip objects.
        media_info: Dict from get_media_info().

    Returns:
        FCPXML string.
    """
    p = Path(media_path)
    fr_num = media_info["frame_rate_num"]
    fr_den = media_info["frame_rate_den"]
    has_video = media_info["has_video"]
    has_audio = media_info["has_audio"]

    fps = fr_num / fr_den

    def _frames_to_rational(frames):
        # Frame-exact rational: frames * (den/num) seconds.
        return f"{frames * fr_den}/{fr_num}s"

    # Format spec — mirror auto-editor: a recognized name (or RateUndefined),
    # plus frameDuration/width/height AND colorSpace. FCP rejects the sequence's
    # format reference when colorSpace is missing on a non-standard format.
    color_space = media_info.get("color_space", "1-1-1 (Rec. 709)")
    if has_video:
        w = media_info["width"] or 1920
        h = media_info["height"] or 1080
        fmt_name = _fcp_format_name(w, h, fr_num, fr_den)
        format_el = (f'    <format id="r1" name="{fmt_name}" '
                     f'frameDuration="{fr_den}/{fr_num}s" '
                     f'width="{w}" height="{h}" colorSpace="{color_space}"/>')
    else:
        # Audio-only: no video format name, no dimensions.
        format_el = f'    <format id="r1" frameDuration="{fr_den}/{fr_num}s"/>'

    # Asset — duration is the FULL source media duration (clips reference into
    # it), expressed in exact frames. FCPXML 1.11 uses a <media-rep> child.
    source_dur_frames = max(1, round(media_info.get("duration", 0) * fps))
    tl_dur_str = _frames_to_rational(source_dur_frames)
    file_url = f"file://{xml_escape(str(p.resolve()))}"
    media_rep = f'      <media-rep kind="original-media" src="{file_url}"/>'

    has_video_str = "1" if has_video else "0"
    has_audio_str = "1" if has_audio else "0"
    asset_el = f'    <asset id="r2" name="{xml_escape(p.stem)}" start="0s" hasVideo="{has_video_str}" format="r1" hasAudio="{has_audio_str}" audioSources="1" audioChannels="2" duration="{tl_dur_str}">\n{media_rep}\n    </asset>'

    # Build spine clips — accumulate the timeline position in INTEGER FRAMES so
    # every clip's offset equals the exact sum of prior durations. Rounding each
    # clip's offset and duration independently from floats (the old approach)
    # produced ±1-frame gaps/overlaps that FCP imports as spurious 1–2 frame clips.
    spine_items = []
    timeline_frames = 0
    for clip in clips:
        in_frame = round(clip.source_in * fps)
        dur_frames = round(clip.duration * fps)
        if dur_frames <= 0:
            continue  # degenerate clip — skip rather than emit a 0-length item
        offset = _frames_to_rational(timeline_frames)
        start = _frames_to_rational(in_frame)
        dur = _frames_to_rational(dur_frames)
        spine_items.append(
            f'          <asset-clip name="{xml_escape(p.stem)}" ref="r2" offset="{offset}" duration="{dur}" start="{start}" tcFormat="NDF"/>'
        )
        timeline_frames += dur_frames

    spine_xml = "\n".join(spine_items)

    # Audio layout — match auto-editor's approach
    sample_rate = media_info.get("sample_rate", 48000)
    audio_rate = "44.1k" if sample_rate == 44100 else "48k"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.11">
  <resources>
{format_el}
{asset_el}
  </resources>
  <library>
    <event name="PaperCut Import">
      <project name="{xml_escape(p.stem)}_ALTERED">
        <sequence format="r1" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="{audio_rate}">
          <spine>
{spine_xml}
          </spine>
        </sequence>
      </project>
    </event>
  </library>
</fcpxml>
"""


def generate_premiere_xml(media_path, clips, media_info):
    """Generate FCP7 XML (Premiere Pro compatible) string.

    Args:
        media_path: Path to the source media file.
        clips: List of Clip objects.
        media_info: Dict from get_media_info().

    Returns:
        FCP7 XML string.
    """
    p = Path(media_path)
    filename = p.name
    fr = media_info["frame_rate"]
    duration_frames = round(media_info["duration"] * fr)
    has_video = media_info["has_video"]
    has_audio = media_info["has_audio"]
    w = media_info.get("width") or 1920
    h = media_info.get("height") or 1080
    timebase = round(fr)

    # Build clip items
    video_clips = []
    audio_clips = []
    timeline_frame = 0
    for i, clip in enumerate(clips, 1):
        in_frame = round(clip.source_in * fr)
        out_frame = round(clip.source_out * fr)
        clip_dur = out_frame - in_frame
        start_frame = timeline_frame
        end_frame = timeline_frame + clip_dur

        clip_xml = f"""          <clipitem id="clipitem-{i}">
            <name>{xml_escape(filename)}</name>
            <duration>{duration_frames}</duration>
            <rate><timebase>{timebase}</timebase><ntsc>FALSE</ntsc></rate>
            <in>{in_frame}</in>
            <out>{out_frame}</out>
            <start>{start_frame}</start>
            <end>{end_frame}</end>
            <file id="file-1"/>
          </clipitem>"""

        if has_video:
            video_clips.append(clip_xml)
        if has_audio:
            audio_clips.append(clip_xml.replace(
                f'id="clipitem-{i}"', f'id="clipitem-audio-{i}"'
            ))

        timeline_frame = end_frame

    total_tl_frames = timeline_frame

    video_track = ""
    if has_video and video_clips:
        video_track = f"""      <track>
{chr(10).join(video_clips)}
      </track>"""

    audio_track = ""
    if has_audio and audio_clips:
        audio_track = f"""      <track>
{chr(10).join(audio_clips)}
      </track>"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence>
    <name>{xml_escape(p.stem)}_ALTERED</name>
    <duration>{total_tl_frames}</duration>
    <rate><timebase>{timebase}</timebase><ntsc>FALSE</ntsc></rate>
    <media>
      <video>
{video_track}
      </video>
      <audio>
{audio_track}
      </audio>
    </media>
    <timecode>
      <string>00:00:00:00</string>
      <frame>0</frame>
      <rate><timebase>{timebase}</timebase><ntsc>FALSE</ntsc></rate>
    </timecode>
  </sequence>
  <bin>
    <children>
      <clip id="masterclip-1">
        <name>{xml_escape(filename)}</name>
        <duration>{duration_frames}</duration>
        <rate><timebase>{timebase}</timebase><ntsc>FALSE</ntsc></rate>
        <file id="file-1">
          <name>{xml_escape(filename)}</name>
          <pathurl>file://localhost{xml_escape(str(p.resolve()))}</pathurl>
          <duration>{duration_frames}</duration>
          <rate><timebase>{timebase}</timebase><ntsc>FALSE</ntsc></rate>
        </file>
      </clip>
    </children>
  </bin>
</xmeml>
"""


#: A clip this short (frames) in the final output means a sliver/rounding bug
#: slipped through — surfaced as a loud warning, not silently shipped.
MAX_BAD_FRAMES = 4


def validate_fcpxml(xml_str, max_bad_frames=MAX_BAD_FRAMES):
    """Sanity-check a generated FCPXML for artifacts FCP would surface.

    Returns a list of human-readable warnings (empty list = clean):
      - any spine clip <= max_bad_frames frames long
      - any boundary where a clip's offset != previous offset + duration
        (a ±1-frame gap/overlap, which FCP renders as a tiny clip)

    This is a last-line guardrail; the sliver filter and integer-frame tiling
    should keep it from ever firing.
    """
    warnings = []
    fd = re.search(r'<format[^>]*frameDuration="(\d+)/(\d+)s"', xml_str)
    if not fd:
        return warnings
    fr_den, fr_num = int(fd.group(1)), int(fd.group(2))
    fps = fr_num / fr_den if fr_den else 0
    if not fps:
        return warnings

    offs, durs = [], []
    for on, od, dn, dd in re.findall(
        r'<asset-clip[^>]*offset="(\d+)/(\d+)s"[^>]*duration="(\d+)/(\d+)s"', xml_str
    ):
        durs.append(round(int(dn) / int(dd) * fps))
        offs.append(round(int(on) / int(od) * fps))

    if not durs:
        return warnings

    short = [d for d in durs if d <= max_bad_frames]
    if short:
        warnings.append(
            f"{len(short)} clip(s) <= {max_bad_frames} frames "
            f"(shortest {min(durs)}f) — sliver/rounding bug suspected"
        )

    mismatches = sum(
        1 for i in range(len(offs) - 1) if offs[i + 1] != offs[i] + durs[i]
    )
    if mismatches:
        warnings.append(
            f"{mismatches} clip-boundary tiling mismatch(es) — would import as "
            f"spurious 1-2 frame gaps/clips"
        )

    return warnings


def export_video(media_path, clips, output_path, extra_args=None):
    """Export video/audio by concatenating clips via ffmpeg.

    Uses filter_complex with trim/atrim + concat for each clip.

    Args:
        media_path: Path to source media.
        clips: List of Clip objects.
        output_path: Path for output file.
        extra_args: Optional list of extra ffmpeg arguments.
    """
    if not clips:
        raise ValueError("No clips to export")

    p = Path(media_path)
    media_info = get_media_info(str(p))
    has_video = media_info["has_video"]
    has_audio = media_info["has_audio"]

    filter_parts = []
    concat_inputs = []

    for i, clip in enumerate(clips):
        if has_video and has_audio:
            filter_parts.append(
                f"[0:v]trim=start={clip.source_in:.6f}:end={clip.source_out:.6f},setpts=PTS-STARTPTS[v{i}];"
            )
            filter_parts.append(
                f"[0:a]atrim=start={clip.source_in:.6f}:end={clip.source_out:.6f},asetpts=PTS-STARTPTS[a{i}];"
            )
            concat_inputs.append(f"[v{i}][a{i}]")
        elif has_audio:
            filter_parts.append(
                f"[0:a]atrim=start={clip.source_in:.6f}:end={clip.source_out:.6f},asetpts=PTS-STARTPTS[a{i}];"
            )
            concat_inputs.append(f"[a{i}]")
        else:
            filter_parts.append(
                f"[0:v]trim=start={clip.source_in:.6f}:end={clip.source_out:.6f},setpts=PTS-STARTPTS[v{i}];"
            )
            concat_inputs.append(f"[v{i}]")

    n = len(clips)
    if has_video and has_audio:
        concat_filter = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[outv][outa]"
        map_args = ["-map", "[outv]", "-map", "[outa]"]
    elif has_audio:
        concat_filter = f"{''.join(concat_inputs)}concat=n={n}:v=0:a=1[outa]"
        map_args = ["-map", "[outa]"]
    else:
        concat_filter = f"{''.join(concat_inputs)}concat=n={n}:v=1:a=0[outv]"
        map_args = ["-map", "[outv]"]

    filter_complex = "".join(filter_parts) + concat_filter

    cmd = [
        "ffmpeg", "-y",
        "-i", str(p),
        "-filter_complex", filter_complex,
    ] + map_args

    if extra_args:
        cmd.extend(extra_args)

    cmd.append(str(output_path))

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg export failed: {result.stderr[:1000]}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Export timeline from clip list.")
    parser.add_argument("media", help="Source media file")
    parser.add_argument("--clips", required=True, help='JSON array of [{"in": 1.0, "out": 3.5}, ...]')
    parser.add_argument("--format", choices=["fcpxml", "premiere", "video"], default="fcpxml")
    parser.add_argument("--output", help="Output file path")
    args = parser.parse_args()

    clip_data = json.loads(args.clips)
    clips = [Clip(source_in=c["in"], source_out=c["out"]) for c in clip_data]
    info = get_media_info(args.media)

    if args.format == "fcpxml":
        print(generate_fcpxml(args.media, clips, info))
    elif args.format == "premiere":
        print(generate_premiere_xml(args.media, clips, info))
    elif args.format == "video":
        out = args.output or str(Path(args.media).stem) + "_ALTERED" + Path(args.media).suffix
        export_video(args.media, clips, out)
        print(f"Exported to {out}")
