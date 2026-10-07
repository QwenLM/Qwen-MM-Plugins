"""Official Omni template, output parsing, and audio-visual evidence regressions."""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

pytest.importorskip("mcp")

import qwen_mm_plugins_api as api
from qwen_mm_plugins_api.omni import (
    _common,
    _prompts,
    omni_asr,
    omni_asr_timestamped,
    omni_av_caption,
    omni_av_grounding,
    omni_multi_speaker_asr,
)
from qwen_mm_plugins_api.omni._speaker_transcript import parse_speaker_transcript
from qwen_mm_plugins_api.omni._srt_transcript import parse_srt_transcript
from shared.video import probe_media

_REMOTE_AUDIO = "https://example.com/audio.wav"
_CAPTION = {
    "summary": "A person closes a door.",
    "scenes": [
        {
            "time_range": {"start_seconds": 0.0, "end_seconds": 3.0},
            "setting": "A room with a door.",
            "events": [
                {
                    "time_range": {"start_seconds": 1.0, "end_seconds": 2.0},
                    "participants": ["person beside the door"],
                    "action": "The person closes the door.",
                    "sounds": ["An impact sound is heard."],
                }
            ],
        }
    ],
}


def _preview(blocks):
    return json.loads(blocks[0]["text"].split("\n", 1)[1])


def test_caption_prompt_contains_valid_scene_event_schema():
    from jsonschema import Draft202012Validator

    schema = json.loads(_prompts.CAPTION_JSON_PROMPT.split("JSON Schema:", 1)[1])
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_CAPTION)


@pytest.mark.parametrize("style", ["sections", "narrative", "json"])
def test_caption_style_uses_matching_official_prompt(style):
    preview = _preview(
        omni_av_caption.handle({"file_path": "https://example.com/video.mp4", "format": style, "dry_run": True})
    )
    message = preview["messages"][0]
    assert message["role"] == "user"
    assert message["content"][0]["type"] == "video_url"
    assert message["content"][1]["text"] == omni_av_caption._PROMPTS[style]
    assert "[FLAG:" not in message["content"][1]["text"]
    assert "Safety Findings" not in message["content"][1]["text"]


def test_caption_defaults_to_sections_and_narrative_returns_prose(monkeypatch):
    preview = _preview(omni_av_caption.handle({"file_path": _REMOTE_AUDIO, "dry_run": True}))
    assert preview["messages"][0]["content"][-1]["text"] == _prompts.SECTIONS_PROMPT
    prose = "A short clip.\n\n[00:00:01:000-00:00:02:000] A person closes the door."
    monkeypatch.setattr(_common, "call_omni_json", lambda **_kw: pytest.fail("must not parse narrative as JSON"))
    monkeypatch.setattr(_common, "call_omni", lambda **_kw: (prose, None))
    assert omni_av_caption.handle({"file_path": _REMOTE_AUDIO, "format": "narrative"}) == [
        {"type": "text", "text": prose}
    ]


def test_caption_json_preserves_reply_and_warns_about_known_duration(monkeypatch, tmp_path):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(omni_av_caption, "run_omni", lambda *_a, **_kw: (_CAPTION, None))
    monkeypatch.setattr(omni_av_caption, "media_duration", lambda _path: 3.0)
    blocks = omni_av_caption.handle({"file_path": str(source), "format": "json"})
    assert json.loads(blocks[0]["text"]) == _CAPTION
    monkeypatch.setattr(omni_av_caption, "media_duration", lambda _path: 2.0)
    blocks = omni_av_caption.handle({"file_path": str(source), "format": "json"})
    assert json.loads(blocks[0]["text"]) == _CAPTION
    assert blocks[1]["text"].startswith("Warning:")
    assert "must not exceed the video duration" in blocks[1]["text"]


@pytest.mark.parametrize("invalid", ["reversed", "outside_scene", "event_order", "scene_order", "extra", "string_time"])
def test_caption_json_retains_model_deviations_with_warning(monkeypatch, invalid):
    data = copy.deepcopy(_CAPTION)
    scene = data["scenes"][0]
    event = scene["events"][0]
    if invalid == "reversed":
        event["time_range"]["end_seconds"] = 0.0
    elif invalid == "outside_scene":
        event["time_range"]["end_seconds"] = 4.0
    elif invalid == "event_order":
        earlier = copy.deepcopy(event)
        earlier["time_range"] = {"start_seconds": 0.0, "end_seconds": 1.0}
        scene["events"].append(earlier)
    elif invalid == "scene_order":
        scene["time_range"] = {"start_seconds": 1.0, "end_seconds": 3.0}
        data["scenes"].append(
            {"time_range": {"start_seconds": 0.0, "end_seconds": 1.0}, "setting": "Room", "events": []}
        )
    elif invalid == "extra":
        event["guess"] = "unsupported"
    else:
        event["time_range"]["start_seconds"] = "1.0"
    monkeypatch.setattr(_common, "call_omni_json", lambda **_kw: data)
    blocks = omni_av_caption.handle({"file_path": _REMOTE_AUDIO, "format": "json"})
    assert json.loads(blocks[0]["text"]) == data
    assert blocks[1]["text"].startswith("Warning:")
    assert not any(block["text"].startswith("Error:") for block in blocks)


def test_speaker_tokens_preserve_words_and_accept_numeric_and_clock_times():
    text = "<soc>\n<sos><0.25>你好，A < B。<1.5><speaker0><eos>\n<sos><00:02.0>Hello!<00:03.25><speaker2><eos><eoc>"
    assert parse_speaker_transcript(text) == [
        {"speaker": "Speaker 0", "start": 0.25, "end": 1.5, "text": "你好，A < B。"},
        {"speaker": "Speaker 2", "start": 2.0, "end": 3.25, "text": "Hello!"},
    ]
    assert parse_speaker_transcript("<soc><eoc>") == []


@pytest.mark.parametrize(
    "text",
    [
        "No speech.",
        "<soc><sos><0>hi<1><speaker1><eos>",
        "<soc><sos><0>hi<1><speaker1><eos><sos><2>partial<eoc>",
        "<soc><sos><2>hi<1><speaker1><eos><eoc>",
        "<soc><sos><nan>hi<1><speaker1><eos><eoc>",
        "<soc><sos><-1>hi<1><speaker1><eos><eoc>",
        "<soc><sos><0><1><speaker1><eos><eoc>",
    ],
)
def test_bad_speaker_output_retains_raw_reply_instead_of_failing(monkeypatch, text):
    monkeypatch.setattr(_common, "call_omni", lambda **_kw: (text, None))
    blocks = omni_multi_speaker_asr.handle({"file_path": _REMOTE_AUDIO})
    assert any(block["text"].startswith("Warning:") for block in blocks)
    assert any(text in block["text"] for block in blocks)
    assert not any(block["text"].startswith("Error:") for block in blocks)
    assert not any("no speech detected" in block["text"] for block in blocks)


def test_multi_speaker_srt_interface_and_hints(monkeypatch):
    captured = {}

    def call(**kwargs):
        captured.update(kwargs)
        return "<soc><sos><0.25>你好<1.5><speaker1><eos><eoc>", None

    monkeypatch.setattr(_common, "call_omni", call)
    monkeypatch.setattr(_common, "call_omni_json", lambda **_kw: pytest.fail("speaker output must use raw text"))
    blocks = omni_multi_speaker_asr.handle(
        {"file_path": _REMOTE_AUDIO, "format": "srt", "num_speakers": 2, "language": "zh"}
    )
    assert "00:00:00,250 --> 00:00:01,500\n[Speaker 1] 你好" in blocks[0]["text"]
    assert json.loads(blocks[1]["text"])["speakers"] == ["Speaker 1"]
    prompt = captured["messages"][0]["content"][-1]["text"]
    assert prompt.startswith(_prompts.MULTI_SPEAKER_PROMPT)
    assert "expected number of speakers is 2" in prompt and "spoken language is zh" in prompt


def test_empty_speaker_tokens_are_no_speech(monkeypatch):
    monkeypatch.setattr(_common, "call_omni", lambda **_kw: ("<soc><eoc>", None))
    blocks = omni_multi_speaker_asr.handle({"file_path": _REMOTE_AUDIO})
    assert json.loads(blocks[0]["text"]) == {"speakers": [], "segments": []}
    assert "no speech detected" in blocks[1]["text"]


@pytest.mark.parametrize(
    "mode,expected_type",
    [(None, "input_audio"), ("audio", "input_audio"), ("auto", "video_url"), ("video", "video_url")],
)
def test_local_multi_speaker_video_defaults_to_audio_and_requires_explicit_av(
    monkeypatch, sample_media_av, mode, expected_type
):
    captured = {}

    def call(**kwargs):
        captured.update(kwargs)
        return "<soc><eoc>", None

    monkeypatch.setattr(_common, "call_omni", call)
    args = {"file_path": sample_media_av}
    if mode is not None:
        args["media_type"] = mode
    omni_multi_speaker_asr.handle(args)
    content = captured["messages"][0]["content"]
    assert content[0]["type"] == expected_type
    if expected_type == "video_url":
        import tempfile
        from base64 import b64decode

        with tempfile.NamedTemporaryFile(suffix=".mp4") as fitted:
            fitted.write(b64decode(content[0]["video_url"]["url"].split(",", 1)[1]))
            fitted.flush()
            assert {stream["codec_type"] for stream in probe_media(fitted.name)["streams"]} == {"video", "audio"}
    preview = _preview(omni_multi_speaker_asr.handle({**args, "dry_run": True}))
    assert preview["messages"][0]["content"][0]["type"] == expected_type


@pytest.mark.parametrize("tool", [omni_asr, omni_asr_timestamped, omni_multi_speaker_asr])
def test_all_asr_tools_default_to_local_audio_only(monkeypatch, sample_media_av, tool):
    captured = {}

    def call(**kwargs):
        captured.update(kwargs)
        return "<soc><eoc>", None

    monkeypatch.setattr(_common, "call_omni", call)
    tool.handle({"file_path": sample_media_av})
    assert [part["type"] for part in captured["messages"][0]["content"]] == ["input_audio", "text"]


@pytest.mark.parametrize("name", ["omni_asr", "omni_asr_timestamped", "omni_multi_speaker_asr"])
def test_asr_schema_defaults_to_audio(name):
    spec = next(spec for spec in api.SPECS if spec.name == name)
    assert spec.input_schema["properties"]["media_type"]["default"] == "audio"
    assert spec.args_model(file_path="/tmp/video.mp4").media_type == "audio"


@pytest.mark.parametrize("tool", [omni_asr, omni_asr_timestamped, omni_multi_speaker_asr])
@pytest.mark.parametrize("mode", ["auto", "video"])
def test_asr_explicit_av_retains_video(monkeypatch, sample_media_av, tool, mode):
    captured = {}

    def call(**kwargs):
        captured.update(kwargs)
        return "<soc><eoc>", None

    monkeypatch.setattr(_common, "call_omni", call)
    tool.handle({"file_path": sample_media_av, "media_type": mode})
    assert captured["messages"][0]["content"][0]["type"] == "video_url"
    preview = _preview(tool.handle({"file_path": sample_media_av, "media_type": mode, "dry_run": True}))
    assert preview["messages"][0]["content"][0]["type"] == "video_url"


def test_multi_speaker_forces_extensionless_remote_video_in_request_and_preview(monkeypatch):
    captured = {}

    def call(**kwargs):
        captured.update(kwargs)
        return "<soc><eoc>", None

    monkeypatch.setattr(_common, "call_omni", call)
    arguments = {"file_path": "https://example.com/download?id=1", "media_type": "video", "fps": 15}
    omni_multi_speaker_asr.handle(arguments)
    part = captured["messages"][0]["content"][0]
    assert part["type"] == "video_url" and part["fps"] == 15
    assert (
        _preview(omni_multi_speaker_asr.handle({**arguments, "dry_run": True}))["messages"][0]["content"][0]["type"]
        == "video_url"
    )


def test_audio_event_uses_official_prompt_and_maps_every_occurrence(monkeypatch):
    captured = {}
    events = [
        {"type": "dog_barking", "start_time": 1.25, "end_time": 2.0},
        {"type": "dog_barking", "start_time": 4.0, "end_time": 4.5},
    ]

    def call(**kwargs):
        captured.update(kwargs)
        return events

    monkeypatch.setattr(_common, "call_omni_json", call)
    args = {"file_path": _REMOTE_AUDIO, "query": "dog_barking", "mode": "audio_event"}
    blocks = omni_av_grounding.handle(args)
    result = json.loads(blocks[0]["text"])
    assert result == {
        "query": "dog_barking",
        "matches": [
            {"type": "dog_barking", "start": 1.25, "end": 2.0},
            {"type": "dog_barking", "start": 4.0, "end": 4.5},
        ],
    }
    assert captured["messages"][0]["content"][-1]["text"] == _prompts.AUDIO_EVENT_PROMPT.replace(
        "[audio event label]", "dog_barking"
    )
    assert len(json.loads(omni_av_grounding.handle({**args, "top_k": 1})[0]["text"])["matches"]) == 1


def test_audio_event_extracts_only_local_audio(monkeypatch, sample_media_av):
    captured = {}

    def call(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(_common, "call_omni_json", call)
    blocks = omni_av_grounding.handle({"file_path": sample_media_av, "query": "bell", "mode": "audio_event"})
    assert captured["messages"][0]["content"][0]["type"] == "input_audio"
    assert json.loads(blocks[0]["text"])["matches"] == []


@pytest.mark.parametrize(
    "value",
    [
        {"type": "bell", "start_time": 1.0, "end_time": 2.0},
        [{"type": "other", "start_time": 1.0, "end_time": 2.0}],
        [{"type": "bell", "start_time": "00:01", "end_time": 2.0}],
        [{"type": "bell", "start_time": 2.0, "end_time": 1.0}],
        [{"type": "bell", "start_time": 1.0, "end_time": 2.0, "score": 0.9}],
        [{"type": "bell", "start_time": float("inf"), "end_time": 2.0}],
    ],
)
def test_audio_event_accepts_model_deviations(monkeypatch, value):
    monkeypatch.setattr(_common, "call_omni_json", lambda **_kw: value)
    blocks = omni_av_grounding.handle({"file_path": _REMOTE_AUDIO, "query": "bell", "mode": "audio_event"})
    result = json.loads(blocks[0]["text"])
    assert len(result["matches"]) == 1
    event = value if isinstance(value, dict) else value[0]
    assert result["matches"][0]["type"] == event["type"]
    if "score" in event:
        assert result["matches"][0]["score"] == event["score"]
    if isinstance(event["start_time"], str):
        assert result["matches"][0]["start"] == 1.0
    assert not any(block["text"].startswith("Error:") for block in blocks)


@pytest.mark.parametrize("tool", ["omni_av_caption", "omni_av_grounding", "omni_multi_speaker_asr", "perceive_media"])
def test_video_sampling_accepts_15_fps_and_rejects_invalid_rates(tool):
    spec = next(spec for spec in api.SPECS if spec.name == tool)
    args = {"file_path": _REMOTE_AUDIO, "prompt": "Describe it", "query": "bell"}
    args = {key: value for key, value in args.items() if key in spec.args_model.model_fields}
    assert spec.input_schema["properties"]["fps"]["maximum"] == 15
    assert spec.args_model(**args, fps=15).fps == 15
    for rate in (0, -1, 15.1, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            spec.args_model(**args, fps=rate)


def test_timestamped_asr_schema_has_no_granularity_selector():
    spec = next(spec for spec in api.SPECS if spec.name == "omni_asr_timestamped")
    assert "granularity" not in spec.input_schema["properties"]
    with pytest.raises(ValidationError):
        spec.args_model(file_path=_REMOTE_AUDIO, granularity="word")
    preview = _preview(omni_asr_timestamped.handle({"file_path": _REMOTE_AUDIO, "dry_run": True}))
    assert preview["messages"][0]["content"][-1]["text"] == (
        "Write SRT captions including the numeric counter and timecodes for speech."
    )


@pytest.mark.parametrize("output_format", ["json", "srt"])
def test_utterance_srt_preserves_multiline_text_and_millisecond_times(monkeypatch, output_format):
    transcript = "1\n00:00:01,001 --> 00:00:02,003\n你好，世界。\nHello world!\n\n2\n00:00:03,250 --> 00:00:04,500\nNext utterance.\n"
    calls = []

    def call(**kwargs):
        calls.append(kwargs)
        return transcript, None

    monkeypatch.setattr(_common, "call_omni", call)
    monkeypatch.setattr(_common, "call_omni_json", lambda **_kw: pytest.fail("SRT must be parsed locally"))
    blocks = omni_asr_timestamped.handle({"file_path": _REMOTE_AUDIO, "format": output_format})
    json_index, srt_index = (0, 1) if output_format == "json" else (1, 0)
    assert json.loads(blocks[json_index]["text"]) == {
        "granularity": "utterance",
        "segments": [
            {"start": 1.001, "end": 2.003, "text": "你好，世界。\nHello world!"},
            {"start": 3.25, "end": 4.5, "text": "Next utterance."},
        ],
    }
    assert blocks[srt_index]["text"] == transcript
    assert len(calls) == 1
    assert "max_tokens" not in calls[0] and "temperature" not in calls[0]


def test_srt_parser_accepts_wrapping_and_line_endings_without_rewriting_speech():
    assert parse_srt_transcript("\ufeff```srt\r\n1\r\n00:00:00.250 --> 00:00:01.500\r\nOriginal speech.\r\n```") == [
        {"start": 0.25, "end": 1.5, "text": "Original speech."}
    ]
    assert parse_srt_transcript("") == []


@pytest.mark.parametrize(
    "text",
    [
        "No speech.",
        "00:00:00,000 --> 00:00:01,000\nHello",
        "2\n00:00:00,000 --> 00:00:01,000\nHello",
        "1\n00:00:02,000 --> 00:00:01,000\nHello",
        "1\n00:00:00,000 --> 00:00:00,000\nHello",
        "1\n00:60:00,000 --> 00:60:01,000\nHello",
        "1\n00:00:00,000 --> 00:00:01,000",
        "1\n00:00:00,000 --> 00:00:01,000\nHello\n2\n00:00:02,000 --> 00:00:03,000\nBye",
        "1\n00:00:02,000 --> 00:00:03,000\nHello\n\n2\n00:00:01,000 --> 00:00:02,000\nBye",
    ],
)
def test_timestamped_asr_recovers_cues_or_retains_raw_reply(monkeypatch, text):
    monkeypatch.setattr(_common, "call_omni", lambda **_kw: (text, None))
    blocks = omni_asr_timestamped.handle({"file_path": _REMOTE_AUDIO})
    assert not any(block["text"].startswith("Error:") for block in blocks)
    assert not any("no speech detected" in block["text"] for block in blocks)
    if "Hello" in text:
        data = json.loads(blocks[0]["text"])
        assert data["segments"][0]["text"] == "Hello"
        if "Bye" in text:
            assert data["segments"][1]["text"] == "Bye"
    else:
        assert blocks[0]["text"] == text
        assert blocks[1]["text"].startswith("Warning:")


def test_srt_missing_hours_recovers_real_model_timecodes():
    warnings = []
    segments = parse_srt_transcript(
        "1\n00:00,000 --> 00:03,000\n原始语音。\n\n2\n00:03,000 --> 00:08,000\nNext utterance.",
        warnings=warnings,
    )
    assert segments == [
        {"start": 0.0, "end": 3.0, "text": "原始语音。"},
        {"start": 3.0, "end": 8.0, "text": "Next utterance."},
    ]
    assert warnings


@pytest.mark.parametrize(
    "text",
    [
        "<soc><sos><bad>lost<1><speaker0><eos><sos><2>Keep this.<3><speaker1><eos><eoc>",
        "<soc><sos><0>partial<sos><2>Keep this.<3><speaker1><eos><eoc>",
    ],
)
def test_partial_transcripts_preserve_valid_segments_and_raw_reply(monkeypatch, text):
    monkeypatch.setattr(_common, "call_omni", lambda **_kw: (text, None))
    blocks = omni_multi_speaker_asr.handle({"file_path": _REMOTE_AUDIO})
    assert json.loads(blocks[0]["text"])["segments"] == [
        {"speaker": "Speaker 1", "start": 2.0, "end": 3.0, "text": "Keep this."}
    ]
    assert text in blocks[-1]["text"]


@pytest.mark.parametrize(
    "tool", ["omni_asr", "omni_av_caption", "omni_av_grounding", "omni_av_counting", "omni_music_caption"]
)
def test_structured_tools_return_raw_text_if_json_unparseable_without_extra_call(monkeypatch, tool):
    calls = []
    text = "The model returned prose instead of JSON."

    def call(**kwargs):
        calls.append(kwargs)
        return text, None

    monkeypatch.setattr(_common, "call_omni", call)
    args = {"file_path": _REMOTE_AUDIO, "format": "json", "query": "bell", "target": "bell"}
    blocks = api.get_handler(tool)(args)
    assert blocks[0]["text"] == text
    assert blocks[1]["text"].startswith("Warning:")
    assert len(calls) == 1


def test_unreadable_srt_ranges_do_not_break_rendering_or_renumbering():
    segments = [
        {"start": float("nan"), "end": 1.0, "text": "bad"},
        {"start": 2.0, "end": 1.0, "text": "reversed"},
        {"start": 3.0, "end": 4.0, "text": "usable"},
    ]
    assert _common.segments_to_srt(segments) == "1\n00:00:03,000 --> 00:00:04,000\nusable\n"


def test_srt_without_counters_preserves_numeric_speech():
    assert parse_srt_transcript("00:00,000 --> 00:01,000\n123\n\n00:02,000 --> 00:03,000\n456") == [
        {"start": 0.0, "end": 1.0, "text": "123"},
        {"start": 2.0, "end": 3.0, "text": "456"},
    ]


@pytest.mark.parametrize("separator", ["\n", "\n\n", "\n \t\n"])
@pytest.mark.parametrize("number", ["123", "2"])
@pytest.mark.parametrize("output_format", ["json", "srt"])
def test_srt_without_counters_preserves_multiline_numeric_speech(monkeypatch, separator, number, output_format):
    transcript = f"00:00:00,000 --> 00:00:01,000\nThe code is\n{number}{separator}00:00:02,000 --> 00:00:03,000\nThanks"
    monkeypatch.setattr(_common, "call_omni", lambda **_kw: (transcript, None))
    blocks = omni_asr_timestamped.handle({"file_path": _REMOTE_AUDIO, "format": output_format})
    json_index, srt_index = (0, 1) if output_format == "json" else (1, 0)
    assert json.loads(blocks[json_index]["text"])["segments"][0]["text"] == f"The code is\n{number}"
    assert f"The code is\n{number}\n\n2\n" in blocks[srt_index]["text"]


@pytest.mark.parametrize("separator,counters", [("\n", (1, 2)), ("\n", (8, 9)), ("\n\n", (8, 12))])
def test_srt_distinguishes_real_counters_from_numeric_speech(separator, counters):
    transcript = (
        f"{counters[0]}\n00:00:00,000 --> 00:00:01,000\nThe code is\n123{separator}"
        f"{counters[1]}\n00:00:02,000 --> 00:00:03,000\n456"
    )
    assert [segment["text"] for segment in parse_srt_transcript(transcript)] == ["The code is\n123", "456"]


@pytest.mark.parametrize("first_counter", ["", "1\n"])
def test_srt_missing_next_counter_preserves_numeric_speech_before_blank_line(first_counter):
    transcript = (
        first_counter + "00:00:00,000 --> 00:00:01,000\nThe answer is\n2\n\n00:00:02,000 --> 00:00:03,000\nThanks"
    )
    assert parse_srt_transcript(transcript)[0]["text"] == "The answer is\n2"


@pytest.mark.parametrize(
    "tool,extra,reply",
    [
        (omni_asr, {}, '{"text": "Hello"}'),
        (omni_asr_timestamped, {}, "1\n00:00:00,000 --> 00:00:01,000\nHello"),
        (omni_multi_speaker_asr, {}, "<soc><sos><0>Hello<1><speaker1><eos><eoc>"),
        (omni_av_grounding, {"mode": "audio_event", "query": "bell"}, "[]"),
    ],
)
@pytest.mark.parametrize(
    "source,part_type",
    [
        ("oss://temporary/clip.mp4", "video_url"),
        ("oss://temporary/clip.MP4?signature=abc#clip", "video_url"),
        ("oss://temporary/audio.wav", "input_audio"),
    ],
)
def test_audio_tools_delegate_temporary_oss_to_model_without_local_decoding(
    monkeypatch, tool, extra, reply, source, part_type
):
    import httpx
    import openai

    requests = []

    def respond(request):
        requests.append(request)
        chunk = {"choices": [{"index": 0, "delta": {"content": reply}}]}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n",
        )

    client_type = openai.OpenAI
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: client_type(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))),
    )
    monkeypatch.setattr(_common, "_local_audio_part", lambda *_a, **_kw: pytest.fail("must not decode OSS locally"))
    args = {
        "file_path": source,
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "test",
        **extra,
    }
    blocks = tool.handle(args)
    assert not any(block["text"].startswith("Error:") for block in blocks)
    assert len(requests) == 1
    assert requests[0].headers["X-DashScope-OssResourceResolve"] == "enable"
    part = json.loads(requests[0].content)["messages"][0]["content"][0]
    assert part["type"] == part_type
    assert (part["video_url"]["url"] if part_type == "video_url" else part["input_audio"]["data"]) == source
    preview = _preview(tool.handle({**args, "dry_run": True}))["messages"][0]["content"][0]
    assert preview["type"] == part_type
    if part_type == "video_url":
        assert "server-side" in preview["note"]
    assert len(requests) == 1  # Dry-run must not invoke the model.


@pytest.mark.parametrize("count", ["2", float("nan"), float("inf"), "unknown", {}])
def test_counting_recovers_model_count_deviations(monkeypatch, count):
    data = {"count": count, "occurrences": {"start": "00:01", "end": "00:02", "note": "one event"}}
    monkeypatch.setattr(_common, "call_omni_json", lambda **_kw: data)
    blocks = api.get_handler("omni_av_counting")({"file_path": _REMOTE_AUDIO, "target": "bell"})
    result = json.loads(blocks[0]["text"])
    assert result["count"] == (2 if count == "2" else 1)
    assert result["occurrences"] == [{"start": 1.0, "end": 2.0, "note": "one event"}]
    assert result["raw"]["occurrences"] == data["occurrences"]
    assert blocks[-1]["text"].startswith("Warning:")
