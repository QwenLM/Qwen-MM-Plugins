"""Time queries select the requested window and reject invalid input without fallback."""

import json

import pytest

from qwen_mm_plugins_video_memory.loader import load_toolkit
from qwen_mm_plugins_video_memory.schema import HierarchicalGraphMemory, MacroEvent
from qwen_mm_plugins_video_memory.time_system import DefaultTimeSystem, EgoLifeTimeSystem
from qwen_mm_plugins_video_memory.toolkit import MemoryToolkit
from qwen_mm_plugins_video_memory.tools.search_by_time import SearchByTimeArgs


@pytest.fixture
def toolkit():
    return MemoryToolkit(
        HierarchicalGraphMemory(
            macro_events=[
                MacroEvent("opening", "Opening", [0, 60]),
                MacroEvent("middle", "Middle", [1800, 1860]),
                MacroEvent("ending", "Ending", [5400, 5460]),
            ]
        )
    )


@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({}, ["opening"]),
        ({"start_sec": 1800, "end_sec": 1920}, ["middle"]),
        ({"start_time": "00:30:00", "end_time": "00:32:00"}, ["middle"]),
        ({"start_time": " 00:30:00 ", "end_sec": 1920}, ["middle"]),
        ({"start_sec": 1800, "end_time": "00:32:00"}, ["middle"]),
        ({"start_sec": 0, "end_sec": 60, "start_time": "00:30:00", "end_time": "00:32:00"}, ["middle"]),
        ({"start_sec": 1860, "end_sec": 1860}, ["middle"]),
        ({"start_time": "01:30:00", "end_time": "01:31:00"}, ["ending"]),
    ],
)
def test_time_queries_select_the_requested_window(toolkit, arguments, expected):
    result = json.loads(toolkit.search_by_time(**arguments))
    assert [event["macro_id"] for event in result] == expected


@pytest.mark.parametrize("value", ["", "30:00", "prefix00:30:00", "00:30:00suffix", "00:60:00", "00:30:60", "-1:00:00"])
@pytest.mark.parametrize("field", ["start_time", "end_time"])
def test_invalid_time_strings_do_not_fall_back(toolkit, value, field):
    with pytest.raises(ValueError, match=field):
        toolkit.search_by_time(**{field: value})


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["start_sec", "end_sec"])
def test_invalid_seconds_fail_schema_and_direct_queries(toolkit, value, field):
    with pytest.raises(ValueError, match=field):
        SearchByTimeArgs(**{field: value})
    with pytest.raises(ValueError, match=field):
        toolkit.search_by_time(**{field: value})


@pytest.mark.parametrize("arguments", [{"start_sec": 601}, {"start_time": "00:30:00", "end_time": "00:29:00"}])
def test_reversed_window_fails(toolkit, arguments):
    with pytest.raises(ValueError, match="at or after"):
        toolkit.search_by_time(**arguments)


@pytest.mark.parametrize("timestamp", ["DAY2 10:44:25", "day2_10:44:25", "  day2   10:44:25  "])
def test_egolife_query_uses_loaded_day_offsets(tmp_path, timestamp):
    memory = HierarchicalGraphMemory(
        macro_events=[MacroEvent(f"DAY{day}_A1_JAKE_clip", f"Day {day}", [0, 60]) for day in (1, 2)]
    )
    memory.save(str(tmp_path / "graph_memory.json"))
    toolkit = load_toolkit(str(tmp_path))
    result = json.loads(toolkit.search_by_time(start_time=timestamp, end_time="DAY2 10:45:25"))
    assert [event["macro_id"] for event in result] == ["DAY2_A1_JAKE_clip"]
    assert json.loads(toolkit.search_by_time(start_sec=138665, end_sec=138725)) == result


@pytest.mark.parametrize(
    "timestamp",
    ["DAY0 00:00:00", "DAY1 24:00:00", "DAY1 12:60:00", "DAY1 12:00:60", "DAY1 12:00:00x", "12:00:00"],
)
def test_egolife_rejects_invalid_clock_times(timestamp):
    toolkit = MemoryToolkit(HierarchicalGraphMemory(), egolife_mode=True)
    with pytest.raises(ValueError, match="start_time"):
        toolkit.search_by_time(start_time=timestamp, end_time="DAY2 00:00:00")


@pytest.mark.parametrize("time_system", [DefaultTimeSystem(), EgoLifeTimeSystem()])
def test_time_systems_reject_nonfinite_and_overflowing_values(time_system):
    for value in (-1, float("nan"), float("inf"), 10**400):
        assert time_system.str_to_sec(value) is None
    huge = "9" * 400
    timestamp = f"DAY{huge} 00:00:00" if time_system.mode_name == "egolife" else f"{huge}:00:00"
    assert time_system.str_to_sec(timestamp) is None


def test_normal_videos_can_exceed_twenty_four_hours():
    assert DefaultTimeSystem().str_to_sec("100:00:01") == 360001
