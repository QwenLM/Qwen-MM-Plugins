"""Build valid note objects locally without asking a model to repair formatting."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from shared.video import parse_time

from .schemas import (
    DocumentDraft,
    DocumentPlan,
    DraftStep,
    PlanStep,
    TimedEvent,
    VideoUnderstanding,
    VisualTarget,
)


def _text(value: Any, default: str = "") -> str:
    if not isinstance(value, str):
        return default
    # Frame placeholders are control syntax, not content to publish.
    value = re.sub(r"\{\{frame[^}]*\}\}", "", value, flags=re.IGNORECASE).strip()
    return value or default


def _texts(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return list(dict.fromkeys(text for item in values if (text := _text(item))))


def _time(value: Any, default: float, duration: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = parse_time(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if parsed is None or not math.isfinite(parsed):
        return default
    return round(min(duration, max(0.0, parsed)), 3)


def plain_evidence(text: str) -> str:
    """Keep ordinary prose; never publish a broken JSON payload or an API error as evidence."""
    text = text.strip()
    if len(text) < 20 or text.startswith(("{", "[", "```")):
        return ""
    if re.search(r'"(?:events|summary|steps|error)"\s*:', text):
        return ""
    if re.search(
        r"(?:unauthori[sz]ed|invalid[_ ]api[_ ]key|rate[_ ]limit|connection error|"
        r"request timed? ?out|HTTP [45]\d\d|无法(?:访问|读取|分析|处理)视频|"
        r"(?:cannot|unable to) (?:access|read|analy[sz]e|process) (?:the )?video)",
        text,
        flags=re.IGNORECASE,
    ):
        return ""
    return text


def recover_json_object(text: str) -> dict[str, Any]:
    """Recover only fully decoded values from an interrupted top-level JSON object."""
    start = text.find("{")
    if start < 0:
        raise ValueError("response contains no JSON object")
    decoder = json.JSONDecoder()
    position = start + 1
    result: dict[str, Any] = {}
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        try:
            key, position = decoder.raw_decode(text, position)
        except ValueError:
            break
        if not isinstance(key, str):
            break
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text) or text[position] != ":":
            break
        position += 1
        while position < len(text) and text[position].isspace():
            position += 1
        try:
            value, position = decoder.raw_decode(text, position)
        except ValueError:
            # Preserve completed event/step objects before a stream was interrupted.
            if position < len(text) and text[position] == "[":
                values = []
                position += 1
                while position < len(text):
                    while position < len(text) and text[position].isspace():
                        position += 1
                    try:
                        item, position = decoder.raw_decode(text, position)
                    except ValueError:
                        break
                    values.append(item)
                    while position < len(text) and text[position].isspace():
                        position += 1
                    if position >= len(text) or text[position] != ",":
                        break
                    position += 1
                if values:
                    result[key] = values
            break
        result[key] = value
        while position < len(text) and text[position].isspace():
            position += 1
        if position >= len(text) or text[position] != ",":
            break
        position += 1
    if not result:
        raise ValueError("response contains no recoverable JSON fields")
    return result


def normalize_understanding(raw: Any, *, duration: float, language: str) -> VideoUnderstanding:
    """Repair simple types and aliases while requiring actual returned source evidence."""
    if not isinstance(raw, dict):
        raise ValueError("video understanding did not return an object")
    events: list[TimedEvent] = []
    raw_events = raw.get("events", [])
    if isinstance(raw_events, list):
        for item in raw_events:
            if not isinstance(item, dict):
                continue
            fact = _text(item.get("fact")) or _text(item.get("description")) or _text(item.get("content"))
            if not fact:
                continue
            start = _time(item.get("start", item.get("start_time")), 0.0, duration)
            end = max(start, _time(item.get("end", item.get("end_time")), duration, duration))
            events.append(TimedEvent(start=start, end=end, fact=fact))
    events.sort(key=lambda event: (event.start, event.end))
    summary = _text(raw.get("summary")) or " ".join(event.fact for event in events)
    if not summary:
        raise ValueError("video understanding returned no usable summary or events")
    subject = _text(raw.get("subject")) or _text(raw.get("title")) or summary[:60]
    detected_language = _text(raw.get("language")) or ("zh" if re.search(r"[\u4e00-\u9fff]", summary) else "en")
    audience = _text(raw.get("audience")) or " / ".join(_texts(raw.get("audience"))) or "general audience"
    result = VideoUnderstanding(
        language=detected_language if language == "auto" else language,
        subject=subject,
        summary=summary,
        events=events,
        audience=audience,
        prerequisites=_texts(raw.get("prerequisites")),
        tools=_texts(raw.get("tools")),
        safety=_texts(raw.get("safety")),
        uncertainties=_texts(raw.get("uncertainties")),
        visible_terms=_texts(raw.get("visible_terms")),
    )
    result.validate()
    return result


def _fallback_steps(understanding: VideoUnderstanding, maximum: int) -> list[dict[str, Any]]:
    """Group existing facts to the step limit instead of dropping later source events."""
    if not understanding.events:
        return [{"title": understanding.subject, "instruction": understanding.summary, "start": 0.0, "end": 0.0}]
    events = understanding.events
    group_size = max(1, math.ceil(len(events) / maximum))
    steps = []
    for offset in range(0, len(events), group_size):
        group = events[offset : offset + group_size]
        steps.append(
            {
                "title": group[0].fact[:70],
                "instruction": group[0].fact,
                "details": [event.fact for event in group[1:]],
                "start": group[0].start,
                "end": max(event.end for event in group),
            }
        )
    return steps


def build_document(
    raw: Any,
    understanding: VideoUnderstanding,
    *,
    duration: float,
    max_steps: int,
    title: str | None = None,
) -> tuple[DocumentPlan, DocumentDraft]:
    """Construct the existing plan/draft contracts from one compact, tolerant response."""
    if not isinstance(raw, dict):
        raw = {}
    evidence_steps = _fallback_steps(understanding, max_steps)
    raw_steps = raw.get("steps")
    steps = raw_steps if isinstance(raw_steps, list) else []
    usable: list[dict[str, Any]] = []
    for index, item in enumerate(steps):
        if not isinstance(item, dict):
            continue
        fallback = evidence_steps[min(index, len(evidence_steps) - 1)]
        instruction = _text(item.get("instruction")) or _text(item.get("objective"))
        if not instruction:
            continue
        start = _time(item.get("start", item.get("start_time")), float(fallback["start"]), duration)
        end = max(start, _time(item.get("end", item.get("end_time")), float(fallback["end"]), duration))
        usable.append({**item, "instruction": instruction, "start": start, "end": end})
    if not usable:
        usable = evidence_steps
    usable.sort(key=lambda item: (item["start"], item["end"]))
    # An overlong response is grouped locally, preserving the later instructions.
    if len(usable) > max_steps:
        group_size = math.ceil(len(usable) / max_steps)
        grouped = []
        for offset in range(0, len(usable), group_size):
            group = usable[offset : offset + group_size]
            details = _texts(group[0].get("details"))
            for item in group[1:]:
                details.extend([item["instruction"], *_texts(item.get("details"))])
            grouped.append({**group[0], "end": max(item["end"] for item in group), "details": details})
        usable = grouped
    plan_steps, draft_steps = [], []
    for index, item in enumerate(usable, 1):
        instruction = item["instruction"]
        step_title = _text(item.get("title"), instruction[:70])
        plan_steps.append(
            PlanStep(
                id=index,
                title=step_title,
                objective=instruction,
                start=item["start"],
                end=item["end"],
                visual_targets=[VisualTarget(id=f"step_{index}", role="primary", query=instruction)],
                image_required=False,
            )
        )
        draft_steps.append(
            DraftStep(
                id=index,
                title=step_title,
                instruction=instruction,
                details=_texts(item.get("details")),
                caption=_text(item.get("caption")),
            )
        )
    resolved_title = _text(title) or _text(raw.get("title"), understanding.subject)
    overview = _text(raw.get("overview")) or _text(raw.get("overview_goal")) or understanding.summary
    common = {
        "title": resolved_title,
        "prerequisites": _texts(raw.get("prerequisites")) or understanding.prerequisites,
        "tools": _texts(raw.get("tools")) or understanding.tools,
        "safety": list(dict.fromkeys([*understanding.safety, *_texts(raw.get("safety"))])),
        "common_mistakes": _texts(raw.get("common_mistakes")),
    }
    plan = DocumentPlan(
        **common,
        audience=understanding.audience or "general audience",
        overview_goal=overview,
        steps=plan_steps,
    )
    draft = DocumentDraft(**common, overview=overview, steps=draft_steps, closing=_text(raw.get("closing")))
    plan.validate()
    draft.validate_against(plan)
    return plan, draft
