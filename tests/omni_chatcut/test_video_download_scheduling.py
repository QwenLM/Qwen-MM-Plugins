"""Offline regression tests for overlapping video polling and downloads."""

import threading

import pytest

from qwen_mm_plugins_omni_chatcut.music_to_mv.pipeline import Pipeline


@pytest.mark.parametrize("resume_pending", [False, True])
@pytest.mark.parametrize("download_fails", [False, True])
def test_download_overlaps_polling_without_changing_completion(tmp_path, resume_pending, download_fails):
    pipeline = object.__new__(Pipeline)
    segments = [{"index": index} for index in range(3)]
    pipeline.output_dir = tmp_path
    pipeline.video_config = {"download_workers": 2, "min_repoll_sec": 0}
    pipeline.state = {
        "segments": {
            str(index): {
                "status": "running",
                "execution_signature": "signature",
                "video_url": str(index),
            }
            for index in range(3)
        }
    }
    if resume_pending:
        pipeline.state["segments"]["0"]["status"] = "download_pending"
    existing = tmp_path / "video_segments/raw/segment_002.mp4"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    pipeline.state["segments"]["2"].update(status="succeeded", output_signature="signature")
    pipeline.validate_video_provider_storyboard = lambda indexes: None
    pipeline.selected_segments = lambda indexes: segments
    pipeline.prepare_segment = lambda segment: None
    pipeline.query_interval = lambda: 0
    owner = threading.get_ident()
    saved_threads = []
    pipeline.save_state = lambda: saved_threads.append(threading.get_ident())
    started = threading.Event()
    polled_other = threading.Event()
    downloaded = []
    reviewed = []

    def poll(index):
        if index == 1:
            # A completed/resumed result starts downloading before all polling ends.
            assert started.wait(5)
            polled_other.set()
        record = pipeline.state["segments"][str(index)]
        record["status"] = "download_pending"
        return record

    def download(url, output):
        downloaded.append(url)
        if url == "0":
            started.set()
            # A slow transfer must not block polling the remaining task.
            assert polled_other.wait(5)
            if download_fails:
                raise RuntimeError("transfer failed")
        output.write_bytes(b"video")

    def review(indexes):
        assert all(record["status"] == "succeeded" for record in pipeline.state["segments"].values())
        assert all((existing.parent / f"segment_{index:03d}.mp4").is_file() for index in range(3))
        reviewed.append(indexes)

    pipeline.poll_segment = poll
    pipeline.download = download
    pipeline.run_quality_control = review
    if download_fails:
        with pytest.raises(RuntimeError, match="Video downloads failed"):
            pipeline.run_videos()
        assert pipeline.state["segments"]["0"]["status"] == "download_failed"
        assert not reviewed
    else:
        pipeline.run_videos()
        assert reviewed == [None]
        assert pipeline.state["segments"]["0"]["output_signature"] == "signature"
    assert sorted(downloaded) == ["0", "1"]
    assert existing.read_bytes() == b"existing"
    assert saved_threads and set(saved_threads) == {owner}


@pytest.mark.parametrize("first_fails", [False, True])
def test_download_state_saved_before_remaining_generation_finishes(tmp_path, first_fails):
    import time

    pipeline = object.__new__(Pipeline)
    pipeline.output_dir = tmp_path
    pipeline.video_config = {"download_workers": 1, "min_repoll_sec": 0}
    segments = [{"index": 0}, {"index": 1}]
    pipeline.state = {
        "segments": {
            str(i): {
                "status": "download_pending" if i == 0 else "running",
                "video_url": str(i),
                "execution_signature": "signature",
            }
            for i in range(2)
        }
    }
    pipeline.validate_video_provider_storyboard = lambda indexes: None
    pipeline.selected_segments = lambda indexes: segments
    pipeline.prepare_segment = lambda segment: None
    pipeline.query_interval = lambda: 0
    saved = []
    pipeline.save_state = lambda: saved.append({i: r["status"] for i, r in pipeline.state["segments"].items()})
    expected = "download_failed" if first_fails else "succeeded"
    deadline = time.monotonic() + 5
    calls = []

    def poll(index):
        assert time.monotonic() < deadline, "completed download state was not harvested during polling"
        assert index == 1
        if pipeline.state["segments"]["0"]["status"] == expected:
            assert any(s["0"] == expected and s["1"] == "running" for s in saved)
            pipeline.state["segments"]["1"]["status"] = "download_pending"
        return pipeline.state["segments"]["1"]

    def download(url, output):
        calls.append(url)
        if first_fails and url == "0":
            raise OSError("simulated transfer failure")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")

    pipeline.poll_segment = poll
    pipeline.download = download
    if first_fails:
        with pytest.raises(RuntimeError, match=r"Video downloads failed for segments \[0\]") as exc:
            pipeline.run_videos(quality_control=False)
        assert isinstance(exc.value.__cause__, OSError)
    else:
        pipeline.run_videos(quality_control=False)
    assert calls == ["0", "1"]
    assert pipeline.state["segments"]["1"]["status"] == "succeeded"
    assert any(s["0"] == expected and s["1"] == "running" for s in saved)
