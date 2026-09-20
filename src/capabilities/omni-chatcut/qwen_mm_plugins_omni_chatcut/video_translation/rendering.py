"""Deterministic local rendering for translated dubbing plans."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from shared.syscmd import find_tool
from shared.video import probe_media

from .client import separate_audio, synthesize_speech
from .contracts import _estimated_spoken_duration, _stream_hash, validate_plan
from .project import QA_SCHEMA, REPORT_SCHEMA, file_sha256, project_duration, read_json, write_json

SOURCE_SAMPLE_RATE = 48000
SOURCE_CHANNELS = 2
REFERENCE_SAMPLE_RATE = 24000
DUB_SAMPLE_RATE = 48000
DUB_CHANNELS = 2
MIX_SAMPLE_RATE = 48000
MIX_CHANNELS = 2
VOICE_FADE_IN_SEC = 0.005
VOICE_FADE_OUT_SEC = 0.020
MAX_VOICE_SPEEDUP = 1.18
MAX_TTS_CANDIDATES = 3
CLEARLY_LONG_ESTIMATED_SPEEDUP = 1.35


def _run(command: list[str], *, timeout: int = 1800) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "media command failed").strip()[-2000:])


def _duration(path: Path) -> float:
    return float((probe_media(str(path)).get("format") or {}).get("duration") or 0.0)


def _extract_audio(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            find_tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(SOURCE_SAMPLE_RATE),
            "-ac",
            str(SOURCE_CHANNELS),
            str(output),
        ]
    )


def _source_audio_is_valid(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        streams = probe_media(str(path)).get("streams") or []
    except Exception:  # noqa: BLE001
        return False
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    return bool(
        audio
        and audio.get("codec_name") == "pcm_s16le"
        and int(audio.get("sample_rate") or 0) == SOURCE_SAMPLE_RATE
        and int(audio.get("channels") or 0) == SOURCE_CHANNELS
    )


def _audio_format_is(path: Path, sample_rate: int, channels: int) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        streams = probe_media(str(path)).get("streams") or []
    except Exception:  # noqa: BLE001
        return False
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    return bool(
        audio and int(audio.get("sample_rate") or 0) == sample_rate and int(audio.get("channels") or 0) == channels
    )


def _extract_reference(source: Path, output: Path, start: float, end: float, full_duration: float) -> None:
    ref_start = max(0.0, start)
    ref_end = min(full_duration, end)
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            find_tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-ss",
            f"{ref_start:.3f}",
            "-t",
            f"{max(0.1, ref_end - ref_start):.3f}",
            "-i",
            str(source),
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(REFERENCE_SAMPLE_RATE),
            "-ac",
            "1",
            str(output),
        ]
    )


def _atempo_chain(factor: float) -> str:
    parts: list[float] = []
    remaining = factor
    while remaining > 2.0:
        parts.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        parts.append(0.5)
        remaining /= 0.5
    parts.append(remaining)
    return ",".join(f"atempo={item:.8f}" for item in parts)


def _loudnorm_two_pass(source: Path, output: Path, duration: float) -> dict[str, float]:
    """Apply measured EBU R128 normalization instead of guessing from short clips."""
    target_i = -16.0
    target_tp = -1.5
    target_lra = 11.0
    measure = subprocess.run(
        [
            find_tool("ffmpeg"),
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-af",
            f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if measure.returncode != 0:
        raise RuntimeError((measure.stderr or "loudness measurement failed").strip()[-2000:])
    matches = re.findall(r"\{\s*\"input_i\".*?\}", measure.stderr, flags=re.DOTALL)
    if not matches:
        raise RuntimeError("ffmpeg loudnorm did not return measured loudness")
    measured = json.loads(matches[-1])
    required = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    if any(key not in measured for key in required):
        raise RuntimeError("ffmpeg loudnorm returned incomplete measurements")
    output.parent.mkdir(parents=True, exist_ok=True)
    filters = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:"
        f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
        f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:linear=true,"
        f"apad,atrim=duration={duration:.6f}"
    )
    _run(
        [
            find_tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            filters,
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(MIX_SAMPLE_RATE),
            "-ac",
            str(MIX_CHANNELS),
            str(output),
        ]
    )
    return {"integrated_lufs": target_i, "true_peak_dbtp": target_tp, "lra": target_lra}


def _fit_voice(source: Path, output: Path, target_duration: float) -> dict[str, Any]:
    natural = _duration(source)
    if natural <= 0 or target_duration <= 0:
        raise ValueError("voice and target durations must be positive")
    requested = natural / target_duration
    if requested > MAX_VOICE_SPEEDUP:
        raise ValueError(
            f"translated speech is too long for its slot: natural={natural:.3f}s, slot={target_duration:.3f}s; "
            "shorten translated_text so its spoken duration more closely matches source_text"
        )
    # Preserve the TTS model's natural pace when speech is shorter than its slot.
    # Slowing each independently generated line by a different amount makes the
    # assembled voice track sound pasted together. Only accelerate overlong speech.
    applied = max(1.0, requested)
    spoken_duration = min(target_duration, natural / applied)
    remaining_silence = max(0.0, target_duration - spoken_duration)
    leading_silence = remaining_silence / 2
    trailing_silence = remaining_silence - leading_silence
    leading_samples = round(leading_silence * DUB_SAMPLE_RATE)
    fade_in = min(VOICE_FADE_IN_SEC, spoken_duration / 4)
    fade_out = min(VOICE_FADE_OUT_SEC, spoken_duration / 4)
    fade_out_start = max(0.0, spoken_duration - fade_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dub-fit-", dir=output.parent) as temporary:
        paced = Path(temporary) / "paced.wav"
        filters = (
            f"{_atempo_chain(applied)},"
            f"afade=t=in:st=0:d={fade_in:.6f},"
            f"afade=t=out:st={fade_out_start:.6f}:d={fade_out:.6f},"
            f"atrim=duration={spoken_duration:.6f}"
        )
        _run(
            [
                find_tool("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-af",
                filters,
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(DUB_SAMPLE_RATE),
                "-ac",
                str(DUB_CHANNELS),
                str(paced),
            ]
        )
        _run(
            [
                find_tool("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-i",
                str(paced),
                "-af",
                f"adelay={leading_samples}S|{leading_samples}S,apad,atrim=duration={target_duration:.6f}",
                "-c:a",
                "pcm_s16le",
                "-ar",
                str(DUB_SAMPLE_RATE),
                "-ac",
                str(DUB_CHANNELS),
                str(output),
            ]
        )
    return {
        "natural_duration_sec": round(natural, 3),
        "target_duration_sec": round(target_duration, 3),
        "atempo": round(applied, 4),
        "stretch_ratio": 1.0,
        "speech_fill_ratio": round(spoken_duration / target_duration, 4),
        "leading_silence_sec": round(leading_silence, 4),
        "trailing_silence_sec": round(trailing_silence, 4),
        "fade_in_sec": round(fade_in, 4),
        "fade_out_sec": round(fade_out, 4),
        "sample_rate": DUB_SAMPLE_RATE,
        "channels": DUB_CHANNELS,
    }


def _prepare_raw_voice(
    *,
    text: str,
    target_language: str,
    reference_path: Path,
    raw: Path,
    signature_path: Path,
    signature: str,
    signature_value: dict[str, str],
    target_duration: float,
    explicit_server: str | None,
    reuse_existing: bool,
) -> dict[str, Any]:
    estimated_duration = _estimated_spoken_duration(text, target_language)
    estimated_speedup = estimated_duration / target_duration
    clearly_long = estimated_speedup > CLEARLY_LONG_ESTIMATED_SPEEDUP
    attempts: list[dict[str, Any]] = []

    cached_signature = None
    if signature_path.is_file():
        try:
            cached_signature = read_json(signature_path).get("signature")
        except (OSError, ValueError):
            cached_signature = None
    reusable = reuse_existing and raw.is_file() and raw.stat().st_size > 0 and cached_signature == signature
    if reusable:
        cached_duration = _duration(raw)
        cached_speedup = cached_duration / target_duration
        attempts.append(
            {
                "attempt": 0,
                "source": "cache",
                "natural_duration_sec": round(cached_duration, 3),
                "required_speedup": round(cached_speedup, 4),
            }
        )
        if cached_speedup <= MAX_VOICE_SPEEDUP:
            return {
                "tts_generated_attempts": 0,
                "tts_candidate_durations_sec": [round(cached_duration, 3)],
                "raw_tts_reused": True,
                "estimated_speedup": round(estimated_speedup, 4),
            }

    allowed_new_attempts = (0 if attempts else 1) if clearly_long else MAX_TTS_CANDIDATES - len(attempts)
    raw.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dub-tts-candidates-", dir=raw.parent) as temporary:
        for attempt_index in range(1, allowed_new_attempts + 1):
            candidate = Path(temporary) / f"attempt_{attempt_index:02d}.wav"
            synthesize_speech(
                text=text,
                reference_audio=str(reference_path),
                output_path=str(candidate),
                explicit_server=explicit_server,
            )
            candidate_duration = _duration(candidate)
            if candidate_duration <= 0:
                raise RuntimeError("dubbing service returned TTS audio with no measurable duration")
            required_speedup = candidate_duration / target_duration
            attempts.append(
                {
                    "attempt": attempt_index,
                    "source": "generated",
                    "natural_duration_sec": round(candidate_duration, 3),
                    "required_speedup": round(required_speedup, 4),
                }
            )
            if required_speedup <= MAX_VOICE_SPEEDUP:
                raw.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(candidate, raw)
                write_json(
                    signature_path,
                    {"signature": signature, "inputs": signature_value, "selected_attempt": attempt_index},
                )
                return {
                    "tts_generated_attempts": sum(item["source"] == "generated" for item in attempts),
                    "tts_candidate_durations_sec": [item["natural_duration_sec"] for item in attempts],
                    "raw_tts_reused": False,
                    "estimated_speedup": round(estimated_speedup, 4),
                }

    durations = ", ".join(f"{item['natural_duration_sec']:.3f}s" for item in attempts)
    if clearly_long:
        reason = (
            f"translated text is estimated to require {estimated_speedup:.2f}x speed, "
            f"above the {CLEARLY_LONG_ESTIMATED_SPEEDUP:.2f}x retry threshold"
        )
    else:
        reason = f"all {len(attempts)} TTS candidates exceeded the {MAX_VOICE_SPEEDUP:.2f}x speed limit"
    raise ValueError(
        f"translated speech is too long for its slot: {reason}; candidate durations=[{durations}], "
        f"slot={target_duration:.3f}s; shorten translated_text"
    )


def _slot(segments: list[dict[str, Any]], index: int, full_duration: float) -> tuple[float, float]:
    segment = segments[index]
    start = float(segment["start_sec"])
    end = float(segment["end_sec"])
    previous_end = float(segments[index - 1]["end_sec"]) if index else 0.0
    next_start = float(segments[index + 1]["start_sec"]) if index + 1 < len(segments) else full_duration
    before = min(0.3, max(0.0, start - previous_end) / 2)
    after = min(0.3, max(0.0, next_start - end) / 2)
    return max(0.0, start - before), min(full_duration, end + after)


def _timeline_diagnostics(segments: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    """Validate the actual render slots before any TTS or mixing work starts."""
    errors: list[str] = []
    slots: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, segment in enumerate(segments):
        start, end = _slot(segments, index, duration)
        segment_id = str(segment.get("segment_id") or f"DUB_{index + 1:04d}")
        if end <= start:
            errors.append(f"{segment_id} has a non-positive render slot")
        if start < previous_end - 1e-6:
            errors.append(f"{segment_id} render slot overlaps the previous slot")
        if start < -1e-6 or end > duration + 1e-6:
            errors.append(f"{segment_id} render slot is outside the source duration")
        slots.append(
            {
                "segment_id": segment_id,
                "start_sec": round(start, 6),
                "end_sec": round(end, 6),
                "duration_sec": round(max(0.0, end - start), 6),
            }
        )
        previous_end = end
    return {"valid": not errors, "errors": errors, "slots": slots}


def _segment_risk_flags(
    segment: dict[str, Any],
    fit: dict[str, Any],
    reference_duration: float,
    tts_result: dict[str, Any] | None = None,
) -> list[str]:
    flags: list[str] = []
    actual_speedup = float(fit.get("atempo") or 1.0)
    stretch_ratio = float(fit.get("stretch_ratio") or 1.0)
    speech_fill_ratio = float(fit.get("speech_fill_ratio") or 1.0)
    if actual_speedup > 1.12:
        flags.append("acceleration_above_1.12x")
    if actual_speedup > MAX_VOICE_SPEEDUP + 1e-6:
        flags.append("acceleration_exceeds_limit")
    if stretch_ratio > 1.18:
        flags.append("stretch_above_1.18x")
    if speech_fill_ratio < 0.6:
        flags.append("speech_fill_below_0.60")
    if reference_duration < 1.5:
        flags.append("short_voice_reference")
    if len(segment.get("source_segment_ids") or []) > 1:
        flags.append("merged_source_segments")
    silence = float(fit.get("leading_silence_sec") or 0.0) + float(fit.get("trailing_silence_sec") or 0.0)
    if silence > 0.4:
        flags.append("large_boundary_silence")
    if int((tts_result or {}).get("tts_generated_attempts") or 0) > 1:
        flags.append("tts_retry")
    return flags


RISK_MESSAGES = {
    "acceleration_above_1.12x": "speech was accelerated above 1.12x; listen for rushed delivery",
    "acceleration_exceeds_limit": "speech exceeded the configured acceleration limit",
    "stretch_above_1.18x": "speech was stretched substantially; listen for unnatural pacing",
    "speech_fill_below_0.60": "speech fills less than 60% of its slot; consider expanding the translation or regrouping",
    "short_voice_reference": "voice reference is shorter than 1.5 seconds; check speaker identity",
    "merged_source_segments": "multiple source segments were merged into one dubbing unit",
    "large_boundary_silence": "large padding silence was added around the speech",
    "tts_retry": "TTS needed multiple candidates; verify the selected delivery",
    "low_vad_overlap": "speech timing has low VAD overlap; verify the source boundary",
}


def _risk_messages(flags: list[str]) -> list[str]:
    return [RISK_MESSAGES.get(flag, flag) for flag in flags]


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        shutil.move(temporary, path)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def _build_diagnostics(
    root: Path,
    segment_reports: list[dict[str, Any]],
    timeline: dict[str, Any],
    duration: float,
) -> dict[str, Any]:
    review_segments = [item for item in segment_reports if item.get("risk_flags")]
    review_items = [
        {
            "segment_id": item["segment_id"],
            "slot_start_sec": item["slot_start_sec"],
            "slot_end_sec": item["slot_end_sec"],
            "source_text": item.get("source_text"),
            "translated_text": item.get("translated_text"),
            "reasons": item.get("risk_messages") or [],
            "risk_flags": item.get("risk_flags") or [],
            "reference_audio": item.get("reference_audio"),
        }
        for item in review_segments
    ]
    diagnostics = {
        "schema": "omni-chatcut/video-translation-diagnostics/v1",
        "duration_sec": duration,
        "segment_count": len(segment_reports),
        "timeline": timeline,
        "summary": {
            "pass_count": len(segment_reports) - len(review_segments),
            "review_count": len(review_segments),
            "risk_count": sum(len(item.get("risk_flags") or []) for item in review_segments),
        },
        "review_segments": review_items,
        "segments": [
            {
                "segment_id": item["segment_id"],
                "slot_start_sec": item["slot_start_sec"],
                "slot_end_sec": item["slot_end_sec"],
                "translated_text": item["translated_text"],
                "natural_duration_sec": item.get("natural_duration_sec"),
                "target_duration_sec": item.get("target_duration_sec"),
                "atempo": item.get("atempo"),
                "stretch_ratio": item.get("stretch_ratio"),
                "speech_fill_ratio": item.get("speech_fill_ratio"),
                "leading_silence_sec": item.get("leading_silence_sec"),
                "trailing_silence_sec": item.get("trailing_silence_sec"),
                "reference_audio": item.get("reference_audio"),
                "risk_flags": item.get("risk_flags") or [],
                "risk_messages": item.get("risk_messages") or [],
                "source_text": item.get("source_text"),
                "tts_generated_attempts": item.get("tts_generated_attempts"),
                "raw_tts_reused": item.get("raw_tts_reused"),
                "status": "review" if item.get("risk_flags") else "pass",
            }
            for item in segment_reports
        ],
    }
    write_json(root / "full/translation_diagnostics.json", diagnostics)
    lines = [
        "# Video Translation Summary",
        "",
        f"- Segments: {diagnostics['segment_count']}",
        f"- Passed: {diagnostics['summary']['pass_count']}",
        f"- Needs review: {diagnostics['summary']['review_count']}",
        f"- Duration: {duration:.3f}s",
        "",
        "## Needs manual review",
        "",
    ]
    if review_items:
        for item in review_items:
            slot = f"{item['slot_start_sec']:.3f}–{item['slot_end_sec']:.3f}s"
            lines.append(f"- **{item['segment_id']} ({slot})**: {'; '.join(item['reasons'])}")
            lines.append(f"  - Source: {item['source_text']}")
            lines.append(f"  - English: {item['translated_text']}")
            lines.append(f"  - Reference: `{item['reference_audio']}`")
    else:
        lines.append("No automatic risk flags were found. A normal end-to-end listening check is still required.")
    lines.extend(
        [
            "",
            "## All segments",
            "",
            "| Segment | Slot | Translation | Timing | Status | Risks |",
            "|---|---:|---|---:|---|---|",
        ]
    )
    for item in diagnostics["segments"]:
        slot = f"{item['slot_start_sec']:.3f}–{item['slot_end_sec']:.3f}s"
        timing = f"{item['atempo']:.2f}x / {item['stretch_ratio']:.2f}x"
        text_value = str(item["translated_text"]).replace("|", "\\|")
        risks = ", ".join(item["risk_messages"]) or "—"
        lines.append(f"| {item['segment_id']} | {slot} | {text_value} | {timing} | {item['status']} | {risks} |")
    lines.extend(
        [
            "",
            "## Review guidance",
            "",
            "Listen to every segment marked `review`, then do one full end-to-end pass for ordering, overlaps, pronunciation, and mix balance.",
            "",
            "Generated artifacts: `translation_diagnostics.json` contains machine-readable details; `render_report.json` contains the full render record.",
        ]
    )
    _write_text_atomic(root / "full/translation_summary.md", "\n".join(lines) + "\n")
    return diagnostics


def _prepare_background(source: Path, output: Path, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            find_tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            f"apad,atrim=duration={duration:.6f}",
            "-c:a",
            "pcm_s16le",
            "-ar",
            str(MIX_SAMPLE_RATE),
            "-ac",
            str(MIX_CHANNELS),
            str(output),
        ]
    )


def _mix(background: Path | None, voices: list[tuple[Path, float]], output: Path, duration: float) -> None:
    if background is not None and not _audio_format_is(background, MIX_SAMPLE_RATE, MIX_CHANNELS):
        raise ValueError(f"background must be {MIX_SAMPLE_RATE} Hz stereo before mixing")
    for voice, _ in voices:
        if not _audio_format_is(voice, DUB_SAMPLE_RATE, DUB_CHANNELS):
            raise ValueError(f"fitted voice must be {DUB_SAMPLE_RATE} Hz stereo before mixing: {voice}")
    if background is None and not voices:
        raise ValueError("mix requires a background track or at least one fitted voice")

    # Render the non-overlapping dubbing slots as one chronological stream.  A
    # previous implementation delayed every voice independently and fed all of
    # them to one large amix graph.  FFmpeg 7.1 can flush the final delayed
    # input at timestamp zero when that input ends very close to the requested
    # output duration.  Concatenating the known gaps and voices avoids delayed
    # timestamps entirely; amix then only ever combines background + voice bus.
    ordered_voices = sorted(voices, key=lambda item: item[1])
    command = [find_tool("ffmpeg"), "-v", "error", "-y"]
    filters: list[str] = []
    input_index = 0
    if background is not None:
        command.extend(["-i", str(background)])
        filters.append(
            f"[0:a]aformat=sample_rates={MIX_SAMPLE_RATE}:sample_fmts=fltp:channel_layouts=stereo,"
            f"apad,atrim=duration={duration:.6f}[bg]"
        )
        input_index = 1
    for voice, _ in ordered_voices:
        command.extend(["-i", str(voice)])

    timeline_labels: list[str] = []
    cursor = 0.0
    sample_tolerance = 1.0 / DUB_SAMPLE_RATE
    for voice_number, (voice, start) in enumerate(ordered_voices, start=1):
        index = input_index + voice_number - 1
        start = float(start)
        voice_duration = _duration(voice)
        if start < -sample_tolerance:
            raise ValueError(f"voice start must not be negative: {voice}")
        if start < cursor - sample_tolerance:
            raise ValueError(f"fitted voices overlap near {start:.6f}s: {voice}")
        gap = max(0.0, start - cursor)
        if gap > sample_tolerance:
            gap_label = f"gap{voice_number}"
            filters.append(
                f"anullsrc=r={DUB_SAMPLE_RATE}:cl=stereo:d={gap:.9f},"
                f"aformat=sample_fmts=fltp,asetpts=PTS-STARTPTS[{gap_label}]"
            )
            timeline_labels.append(f"[{gap_label}]")
        voice_label = f"voice{voice_number}"
        filters.append(
            f"[{index}:a]aformat=sample_rates={DUB_SAMPLE_RATE}:sample_fmts=fltp:channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS[{voice_label}]"
        )
        timeline_labels.append(f"[{voice_label}]")
        cursor = start + voice_duration

    if ordered_voices:
        if cursor > duration + sample_tolerance:
            raise ValueError(f"fitted voices extend past the mix duration by {cursor - duration:.6f}s")
        trailing_gap = max(0.0, duration - cursor)
        if trailing_gap > sample_tolerance:
            filters.append(
                f"anullsrc=r={DUB_SAMPLE_RATE}:cl=stereo:d={trailing_gap:.9f},"
                "aformat=sample_fmts=fltp,asetpts=PTS-STARTPTS[tail_gap]"
            )
            timeline_labels.append("[tail_gap]")
        filters.append(
            f"{''.join(timeline_labels)}concat=n={len(timeline_labels)}:v=0:a=1,"
            f"apad,atrim=duration={duration:.6f}[voice_bus]"
        )
        if background is not None:
            filters.append(
                f"[bg][voice_bus]amix=inputs=2:normalize=0:dropout_transition=0,atrim=duration={duration:.6f}[out]"
            )
        else:
            filters.append(f"[voice_bus]atrim=duration={duration:.6f}[out]")
    else:
        filters.append("[bg]anull[out]")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dub-mix-", dir=output.parent) as temporary:
        premaster = Path(temporary) / "premaster.wav"
        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[out]",
                "-c:a",
                "pcm_f32le",
                "-ar",
                str(MIX_SAMPLE_RATE),
                "-ac",
                str(MIX_CHANNELS),
                str(premaster),
            ]
        )
        _run(command)
        _loudnorm_two_pass(premaster, output, duration)


def _remux(source: Path, audio: Path, output: Path, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            find_tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "0:s?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-c:s",
            "copy",
            "-map_metadata",
            "0",
            "-t",
            f"{duration:.6f}",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def render_project(
    project_dir: str,
    *,
    explicit_server: str | None = None,
    reuse_existing: bool = True,
    background_mode: str = "include",
    regenerate_segment_ids: set[str] | None = None,
) -> dict[str, Any]:
    if background_mode not in {"include", "omit"}:
        raise ValueError("background_mode must be 'include' or 'omit'")
    validation = validate_plan(project_dir)
    if not validation["valid"]:
        raise ValueError("translation plan validation failed: " + "; ".join(validation["errors"]))
    root = Path(project_dir).expanduser().resolve()
    plan_path = Path(validation["plan_path"])
    plan = read_json(plan_path)
    manifest = read_json(root / "project.json")
    source = Path(plan["source_movie"])
    if file_sha256(source) != plan["source_sha256"]:
        raise ValueError("source movie content changed after the translation plan was authored")
    duration = project_duration(root, manifest)
    regenerate_segment_ids = set(regenerate_segment_ids or set())
    known_segment_ids = {str(item.get("segment_id")) for item in plan.get("segments") or []}
    unknown_segment_ids = sorted(regenerate_segment_ids - known_segment_ids)
    if unknown_segment_ids:
        raise ValueError(f"unknown regenerate_segment_ids: {', '.join(unknown_segment_ids)}")
    timeline = _timeline_diagnostics(plan["segments"], duration)
    if not timeline["valid"]:
        raise ValueError("render timeline validation failed: " + "; ".join(timeline["errors"]))
    source_audio = root / "work/source/source.wav"
    source_signature_path = root / "work/source/source.signature.json"
    source_signature = {
        "source_sha256": plan["source_sha256"],
        "codec": "pcm_s16le",
        "sample_rate": SOURCE_SAMPLE_RATE,
        "channels": SOURCE_CHANNELS,
    }
    cached_source_signature = None
    if source_signature_path.is_file():
        try:
            cached_source_signature = read_json(source_signature_path)
        except (OSError, ValueError):
            cached_source_signature = None
    source_audio_changed = cached_source_signature != source_signature or not _source_audio_is_valid(source_audio)
    if source_audio_changed:
        _extract_audio(source, source_audio)
        write_json(source_signature_path, source_signature)
    vocals = root / "work/source/vocals.wav"
    background = root / "work/source/no_vocals.wav"
    separation_signature_path = root / "work/source/separation.signature.json"
    separation_signature = {"source_audio_sha256": file_sha256(source_audio), "model": "htdemucs"}
    cached_separation_signature = None
    if separation_signature_path.is_file():
        try:
            cached_separation_signature = read_json(separation_signature_path)
        except (OSError, ValueError):
            cached_separation_signature = None
    reusable_stems = (
        reuse_existing
        and vocals.is_file()
        and vocals.stat().st_size > 0
        and background.is_file()
        and background.stat().st_size > 0
        and cached_separation_signature == separation_signature
    )
    if not reusable_stems:
        separated = separate_audio(str(source_audio), str(root / "work/source"), explicit_server=explicit_server)
        vocals = Path(separated["stems"]["vocals"])
        background = Path(separated["stems"]["no_vocals"])
        write_json(separation_signature_path, separation_signature)

    segment_reports = []
    voices: list[tuple[Path, float]] = []
    segments = plan["segments"]
    for index, segment in enumerate(segments):
        segment_id = segment["segment_id"]
        slot_start, slot_end = _slot(segments, index, duration)
        target_duration = slot_end - slot_start
        reference = segment.get("reference") or {}
        reference_path = root / f"work/references/{segment_id}.wav"
        _extract_reference(
            vocals,
            reference_path,
            float(reference.get("start_sec", segment["start_sec"])),
            float(reference.get("end_sec", segment["end_sec"])),
            duration,
        )
        raw = root / f"work/tts_raw/{segment_id}.wav"
        signature_value = {
            "translated_text": segment["translated_text"],
            "reference_sha256": file_sha256(reference_path),
        }
        signature = hashlib.sha256(
            json.dumps(signature_value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        signature_path = raw.with_suffix(".signature.json")
        tts_result = _prepare_raw_voice(
            text=segment["translated_text"],
            target_language=str(plan.get("target_language") or ""),
            reference_path=reference_path,
            raw=raw,
            signature_path=signature_path,
            signature=signature,
            signature_value=signature_value,
            target_duration=target_duration,
            explicit_server=explicit_server,
            reuse_existing=reuse_existing and segment_id not in regenerate_segment_ids,
        )
        fitted = root / f"work/tts_fitted/{segment_id}.wav"
        fit = _fit_voice(raw, fitted, target_duration)
        reference_duration = max(
            0.0,
            float(reference.get("end_sec", segment["end_sec"]))
            - float(reference.get("start_sec", segment["start_sec"])),
        )
        risk_flags = _segment_risk_flags(segment, fit, reference_duration, tts_result)
        risk_messages = _risk_messages(risk_flags)
        fitted_probe = probe_media(str(fitted)).get("streams") or []
        fitted_audio = next((item for item in fitted_probe if item.get("codec_type") == "audio"), {})
        if (
            int(fitted_audio.get("sample_rate") or 0) != DUB_SAMPLE_RATE
            or int(fitted_audio.get("channels") or 0) != DUB_CHANNELS
        ):
            raise RuntimeError(f"{segment_id} fitted voice must be {DUB_SAMPLE_RATE} Hz mono before mixing")
        voices.append((fitted, slot_start))
        segment_reports.append(
            {
                "segment_id": segment_id,
                "source_segment_ids": segment["source_segment_ids"],
                "merge_reason": segment.get("merge_reason"),
                "speaker": segment["speaker"],
                "source_text": segment["source_text"],
                "translated_text": segment["translated_text"],
                "slot_start_sec": round(slot_start, 3),
                "slot_end_sec": round(slot_end, 3),
                "reference_source_segment_ids": reference["source_segment_ids"],
                "reference_selection_reason": reference["selection_reason"],
                "reference_audio": str(reference_path),
                "raw_voice": str(raw),
                "fitted_voice": str(fitted),
                "risk_flags": risk_flags,
                "risk_messages": risk_messages,
                "status": "review" if risk_flags else "pass",
                **tts_result,
                **fit,
            }
        )
    premix_background = root / "work/audio/background_48k.wav" if background_mode == "include" else None
    if premix_background is not None:
        _prepare_background(background, premix_background, duration)
    mixed = root / "work/audio/mixed.wav"
    _mix(premix_background, voices, mixed, duration)
    diagnostics = _build_diagnostics(root, segment_reports, timeline, duration)
    final = root / "full/translated.mp4"
    _remux(source, mixed, final, duration)
    final_probe = probe_media(str(final))
    final_duration = float((final_probe.get("format") or {}).get("duration") or 0.0)
    streams = final_probe.get("streams") or []
    decode = (
        subprocess.run(
            [find_tool("ffmpeg"), "-v", "error", "-i", str(final), "-f", "null", "-"], capture_output=True, timeout=1800
        ).returncode
        == 0
    )
    source_video_hash = _stream_hash(source, "0:v:0")
    final_video_hash = _stream_hash(final, "0:v:0")
    source_audio_hash = _stream_hash(source, "0:a:0", decoded_audio=True)
    final_audio_hash = _stream_hash(final, "0:a:0", decoded_audio=True)
    checks = {
        "decode": decode,
        "streams": any(item.get("codec_type") == "video" for item in streams)
        and any(item.get("codec_type") == "audio" for item in streams),
        "duration": abs(final_duration - duration) <= 0.35,
        "segment_count": len(segment_reports) == len(segments),
        "premix_audio_format": (
            premix_background is None or _audio_format_is(premix_background, MIX_SAMPLE_RATE, MIX_CHANNELS)
        )
        and all(
            item.get("sample_rate") == DUB_SAMPLE_RATE and item.get("channels") == DUB_CHANNELS
            for item in segment_reports
        ),
        "video_preserved": source_video_hash is not None
        and final_video_hash is not None
        and source_video_hash == final_video_hash,
        "audio_replaced": source_audio_hash is not None
        and final_audio_hash is not None
        and source_audio_hash != final_audio_hash,
    }
    report = {
        "schema": REPORT_SCHEMA,
        "source_movie": str(source),
        "source_sha256": plan["source_sha256"],
        "plan_sha256": file_sha256(plan_path),
        "source_duration_sec": duration,
        "output_video": str(final),
        "output_sha256": file_sha256(final),
        "segment_count": len(segment_reports),
        "segments": segment_reports,
        "background_mode": background_mode,
        "premix_background": str(premix_background) if premix_background is not None else None,
        "premix_sample_rate": MIX_SAMPLE_RATE,
        "mixed_audio": str(mixed),
        "diagnostics": diagnostics,
        "diagnostics_path": str(root / "full/translation_diagnostics.json"),
        "summary_path": str(root / "full/translation_summary.md"),
        "review_segments": diagnostics["review_segments"],
    }
    qa = {
        "schema": QA_SCHEMA,
        "checks": checks,
        "overall_pass": all(checks.values()),
        "source_duration_sec": duration,
        "final_duration_sec": round(final_duration, 3),
    }
    write_json(root / "full/render_report.json", report)
    write_json(root / "full/final_qa.json", qa)
    manifest_path = root / "project.json"
    manifest = read_json(manifest_path)
    manifest.setdefault("stages", {})["rendering"] = "complete" if qa["overall_pass"] else "blocked"
    write_json(manifest_path, manifest)
    if not qa["overall_pass"]:
        raise RuntimeError("render completed but final QA failed")
    return {
        "final_video": str(final),
        "render_report": str(root / "full/render_report.json"),
        "final_qa": str(root / "full/final_qa.json"),
        "segment_count": len(segment_reports),
        "summary": {
            **diagnostics["summary"],
            "message": (
                f"{diagnostics['summary']['review_count']} segment(s) need manual listening review"
                if diagnostics["summary"]["review_count"]
                else "No automatic risk flags; perform a normal end-to-end listening review"
            ),
        },
        "diagnostics": diagnostics,
        "diagnostics_path": str(root / "full/translation_diagnostics.json"),
        "summary_path": str(root / "full/translation_summary.md"),
        "regenerated_segment_ids": sorted(regenerate_segment_ids),
    }
