"""Regression tests for materialized document and video views."""

import base64
import subprocess

from qwen_mm_plugins_core.producers import save_view
from shared.video import extract_frames_by_seeking


def test_save_view_keeps_distinct_frames_with_same_display_timestamp(tmp_path, requires_ffmpeg):
    video = tmp_path / "moving.mkv"
    output_dir = tmp_path / "views"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=100:duration=1",
            "-c:v",
            "ffv1",
            str(video),
        ],
        check=True,
        capture_output=True,
    )

    times = [0.11, 0.14]
    frames = extract_frames_by_seeking(str(video), times, 0, 0)
    assert len(frames) == 2
    assert frames[0][0] == frames[1][0] == 0.1
    frame_bytes = [base64.b64decode(data) for _, data in frames]
    assert frame_bytes[0] != frame_bytes[1]

    result = save_view.handle({"file_path": str(video), "times": times, "output_dir": str(output_dir)})

    files = sorted(output_dir.glob("*.jpg"))
    assert [path.name for path in files] == ["moving_frame_0.1s.jpg", "moving_frame_0.1s_2.jpg"]
    assert [path.read_bytes() for path in files] == frame_bytes
    assert all(str(path) in result[0]["text"] for path in files)
