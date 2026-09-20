"""Exercise shot retiming with real video frames, independently of remote providers."""

import json
import subprocess

import pytest
from conftest import HAS_FFMPEG

from qwen_mm_plugins_omni_chatcut.music_to_mv.pipeline import Pipeline


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe required")
@pytest.mark.parametrize(
    ("source_duration", "target_duration", "source_fps"),
    [(12, 13, 24), (2, 2.5, 30), (2, 1.5, 24), (2, 2, 24)],
    ids=["seedance-12-to-13", "different-source-fps", "trim-longer", "same-duration"],
)
def test_normalization_preserves_motion_and_exact_frames(tmp_path, source_duration, target_duration, source_fps):
    source = tmp_path / "source.mp4"
    # Brightness increases every frame. A frozen tail is observable without visual-model judgment.
    source_frames = source_duration * source_fps
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"nullsrc=s=64x64:r={source_fps}:d={source_duration},geq=lum='16+200*N/{source_frames - 1}':cb=128:cr=128",
            # Longer audio must not determine the video retiming factor.
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={source_duration + 1}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    pipeline = object.__new__(Pipeline)
    pipeline.output_dir = tmp_path
    (tmp_path / "video_segments/normalized").mkdir(parents=True)
    pipeline.state = {"segments": {"0": {"output_path": str(source)}}}
    pipeline.assembly_config = {"fps": 24, "width": 64, "height": 64, "crf": 18}
    pipeline.save_state = lambda: None
    start = 0.13
    output = pipeline.normalize_segment({"index": 0, "start_sec": start, "duration_sec": target_duration})
    expected_frames = round((start + target_duration) * 24) - round(start * 24)
    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(output)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    assert len(probe["streams"]) == 1
    stream = probe["streams"][0]
    assert stream["codec_type"] == "video"
    assert int(stream["nb_read_frames"]) == expected_frames
    assert float(stream["duration"]) == pytest.approx(expected_frames / 24, abs=1e-5)

    # Decode all output frames: check motion continues through the end, and longer inputs are
    # trimmed without speeding up to squeeze in their final frames.
    frames = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(output), "-vf", "scale=1:1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        check=True,
        capture_output=True,
    ).stdout
    assert len(frames) == expected_frames
    assert frames[-1] - frames[int(expected_frames * 0.9)] > 8
    assert frames[-1] - frames[-7] > 1
    if target_duration < source_duration:
        assert frames[-1] < 200
    else:
        assert frames[-1] > 220
