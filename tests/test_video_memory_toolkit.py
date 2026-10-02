"""Behaviour regressions for the video-memory retrieval toolkit (search_by_time windows).

Building real memory needs a video plus an API key, so these drive the toolkit over a small
in-memory graph file instead: the defect is in how a requested time window is resolved, not in
the data.
"""

import json
import logging


def _toolkit(tmp_path, macros):
    from qwen_mm_plugins_video_memory import loader

    graph = tmp_path / "graph_memory.json"
    graph.write_text(json.dumps({"macro_events": macros}), encoding="utf-8")
    return loader.load_toolkit(str(tmp_path), str(graph))


def _ids(result):
    return [item["macro_id"] for item in json.loads(result)]


DEFAULT_MACROS = [
    {"macro_id": "m1", "label": "intro", "time_range": [0.0, 60.0]},
    {"macro_id": "m2", "label": "middle", "time_range": [1800.0, 1860.0]},
    {"macro_id": "m3", "label": "late", "time_range": [5400.0, 5460.0]},
]

EGOLIFE_MACROS = [
    {"macro_id": "DAY1_A1_JAKE_110942", "label": "day1", "time_range": [0.0, 60.0]},
    {"macro_id": "DAY2_A1_JAKE_104425", "label": "day2", "time_range": [100000.0, 100060.0]},
]


def test_numeric_window_picks_the_matching_macro(tmp_path):
    toolkit = _toolkit(tmp_path, DEFAULT_MACROS)
    assert _ids(toolkit.search_by_time(start_sec=1740, end_sec=1920)) == ["m2"]


def test_time_strings_pick_the_same_window(tmp_path):
    """A HH:MM:SS window on a plain (non-EgoLife) memory must not be silently dropped."""
    toolkit = _toolkit(tmp_path, DEFAULT_MACROS)
    assert _ids(toolkit.search_by_time(start_time="00:29:00", end_time="00:32:00")) == ["m2"]


def test_egolife_strings_pick_the_matching_day(tmp_path):
    toolkit = _toolkit(tmp_path, EGOLIFE_MACROS)
    assert toolkit._egolife_mode
    assert _ids(toolkit.search_by_time(start_time="DAY1 11:09:00", end_time="DAY1 11:11:00")) == ["DAY1_A1_JAKE_110942"]
    # The same instant spelled the way time_val_to_sec() and EgoLifeTimeSystem accept it.
    assert _ids(toolkit.search_by_time(start_time="day1_11:09:00", end_time="day1_11:11:00")) == ["DAY1_A1_JAKE_110942"]


def test_unparsable_time_string_warns(tmp_path, caplog):
    """A dropped filter has to be visible: the caller asked for a window and got another one."""
    toolkit = _toolkit(tmp_path, DEFAULT_MACROS)
    with caplog.at_level(logging.WARNING):
        _ids(toolkit.search_by_time(start_time="00:29:00", end_time="half past eleven"))
    assert "unparsable end_time" in caplog.text
