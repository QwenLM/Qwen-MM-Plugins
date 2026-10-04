"""shared.video.format_timestamp: the clock read_video prints beside each frame."""

from shared.video import format_timestamp, parse_time


def test_format_timestamp_basic_forms():
    assert format_timestamp(12.34, 30) == "12.3s"
    assert format_timestamp(75.0, 120) == "1:15.0"
    assert format_timestamp(3725.5, 4000) == "1:02:05.5"


def test_format_timestamp_carries_a_rounded_up_second_into_the_minute():
    # Sampled timestamps are arbitrary floats (start + i * span / (n - 1)), so a frame
    # just under a minute boundary must not print as second 60.
    assert format_timestamp(59.96, 120) == "1:00.0"
    assert format_timestamp(119.97, 120) == "2:00.0"
    assert format_timestamp(3599.96, 4000) == "1:00:00.0"
    assert format_timestamp(3659.96, 4000) == "1:01:00.0"
    assert format_timestamp(59.94, 120) == "0:59.9"


def test_format_timestamp_round_trips_through_parse_time():
    for seconds, max_seconds in [(59.96, 120), (3599.96, 4000), (12.34, 30), (61.25, 120)]:
        assert abs(parse_time(format_timestamp(seconds, max_seconds)) - round(seconds, 1)) < 1e-9
