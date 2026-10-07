"""Real ffmpeg regressions for timestamps in API Omni's submitted media."""

from __future__ import annotations

import base64
import json
import subprocess
from array import array

import pytest

pytest.importorskip("mcp")

from qwen_mm_plugins_api.omni import _common, perceive_media
from shared import omni_media
from shared.video import probe_media


@pytest.fixture(params=[False, True], ids=["constant-fps", "variable-fps"])
def timeline_source(request, tmp_path, requires_ffmpeg):
    source = tmp_path / "source.mp4"
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=3.4:size=160x120:rate=25",
        "-itsoffset",
        "0.72",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2.5",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    if request.param:
        # A one-second gap must remain a gap; transcoding must not close it or fill it with frames.
        command += ["-vf", "select=not(between(t\\,0.6\\,1.6))"]
    command += [
        "-fps_mode:v",
        "vfr",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(source),
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=30)
    return source


def _video_pts(path):
    data = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_frames",
                "-show_entries",
                "frame=best_effort_timestamp_time",
                "-of",
                "json",
                str(path),
            ],
            timeout=30,
        )
    )
    return [float(frame["best_effort_timestamp_time"]) for frame in data["frames"]]


def _audio_onset(path):
    # Decode on the common media timeline, including silence before a delayed audio stream.
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-af",
            "aresample=async=1:first_pts=0",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "s16le",
            "pipe:1",
        ],
        timeout=30,
    )
    samples = array("h", raw)
    # Ignore low-level AAC ringing before the test tone.
    return next(index / 16000 for index, sample in enumerate(samples) if abs(sample) > 500)


@pytest.mark.parametrize(
    "delivery,start,duration",
    [
        ("inline", 0.0, None),
        ("inline", 0.23, 2.65),
        ("perceive", 1.13, 1.75),
        ("oss", 0.23, 2.65),
    ],
)
def test_submitted_video_preserves_frame_times_and_audio_position(
    timeline_source,
    tmp_path,
    monkeypatch,
    delivery,
    start,
    duration,
):
    source = str(timeline_source)
    output = tmp_path / "submitted.mp4"
    if delivery == "inline":
        _common._preprocess_video(
            source,
            str(output),
            200704,
            fps=1.0,
            start_time=start,
            duration=duration,
        )
    elif delivery == "oss":
        uploaded = []

        def upload(path, **_kwargs):
            uploaded.append(path)
            return "https://example.com/prepared.mp4"

        monkeypatch.setattr(omni_media.oss, "upload_and_sign", upload)
        url = _common._transcode_and_upload(
            source,
            str(output),
            200704,
            1.0,
            start_time=start,
            duration=duration,
        )
        assert url == "https://example.com/prepared.mp4"
        assert uploaded == [str(output)]
    else:

        def capture_request(**kwargs):
            content = kwargs["messages"][0]["content"]
            video = next(part for part in content if part["type"] == "video_url")
            assert video["fps"] == 1.0
            assert content[-1] == {"type": "text", "text": "Inspect this interval"}
            output.write_bytes(base64.b64decode(video["video_url"]["url"].split(",", 1)[1]))
            return "ok", None

        monkeypatch.setattr(perceive_media, "call_omni", capture_request)
        blocks = perceive_media.handle(
            {
                "file_path": source,
                "media_type": "video",
                "fps": 1.0,
                "start_time": start,
                "end_time": start + duration,
                "prompt": "Inspect this interval",
                "api_key": "test",
                "base_url": "https://example.com/v1",
            }
        )
        assert blocks[0] == {"type": "text", "text": "ok"}

    source_duration = float(probe_media(source)["format"]["duration"])
    expected_duration = source_duration if duration is None else duration
    actual_duration = float(probe_media(str(output))["format"]["duration"])
    # Container quantization may differ by a native frame, never by the requested sampling period.
    assert actual_duration == pytest.approx(expected_duration, abs=0.04)
    expected_pts = [stamp - start for stamp in _video_pts(source) if start <= stamp < start + expected_duration]
    assert _video_pts(output) == pytest.approx(expected_pts, abs=0.001)
    expected_onset = max(0.0, _audio_onset(source) - start)
    assert _audio_onset(output) == pytest.approx(expected_onset, abs=0.02)
