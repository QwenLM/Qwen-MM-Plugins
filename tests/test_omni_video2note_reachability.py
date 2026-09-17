"""Opt-in live reachability check for the minimal Omni Video2Note path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_omni_video2note import import_pipeline_module, make_short_av_video

from shared.env import get_env

pytestmark = pytest.mark.reachability

RUN_REACHABILITY = os.environ.get("QWEN_MM_RUN_REACHABILITY") == "1"
HAS_DASHSCOPE = bool(get_env("DASHSCOPE_API_KEY"))


@pytest.mark.skipif(
    not RUN_REACHABILITY or not HAS_DASHSCOPE,
    reason="set QWEN_MM_RUN_REACHABILITY=1 and DASHSCOPE_API_KEY to run the live Omni check",
)
def test_minimal_real_omni_understanding_path(tmp_path: Path):
    config_module = import_pipeline_module("config")
    gateway = import_pipeline_module("model_gateway")
    media = import_pipeline_module("media")

    video = make_short_av_video(tmp_path / "short-av.mp4", duration=1.5)
    probe = media.probe_video(video)
    config = config_module.PipelineConfig(
        video_path=video,
        output_path=tmp_path / "unused.pdf",
        language="en",
        max_iterations=1,
    )
    chunk = media.MediaChunk(
        path=video,
        source_start=0.0,
        source_end=probe.duration,
        delivery={"kind": "inline", "source": str(video), "bytes": video.stat().st_size},
    )

    understanding = gateway.understand_video(config, probe, [chunk])
    understanding.validate()
    assert understanding.subject.strip()
    assert understanding.summary.strip()
