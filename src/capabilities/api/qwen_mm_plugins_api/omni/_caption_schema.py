"""The recommended Omni scene/event schema and evidence timeline validation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class TimeRange(_StrictModel):
    start_seconds: float = Field(ge=0, description="Start time in seconds relative to the beginning of the video")
    end_seconds: float = Field(ge=0, description="End time in seconds; must not precede the start time")

    @model_validator(mode="after")
    def ordered(self) -> TimeRange:
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must not precede start_seconds")
        return self


class Event(_StrictModel):
    time_range: TimeRange = Field(description="Time range of the event")
    participants: list[str] = Field(
        description="People, animals, or objects involved, named by observable features; "
        "use consistent names for the same participant"
    )
    action: str = Field(description="Specific actions, interactions, and observable outcomes")
    sounds: list[str] = Field(description="Sounds heard during the event; use an empty list if none are discernible")


class Scene(_StrictModel):
    time_range: TimeRange = Field(description="Time range of the scene")
    setting: str = Field(description="Environment, spatial layout, and main visual features")
    events: list[Event] = Field(description="Events in chronological order; use an empty list if there are none")

    @model_validator(mode="after")
    def event_times(self) -> Scene:
        previous = self.time_range.start_seconds
        for event in self.events:
            times = event.time_range
            if times.start_seconds < previous:
                raise ValueError("events must be in chronological order")
            if times.end_seconds > self.time_range.end_seconds:
                raise ValueError("each event must fall within its parent scene")
            previous = times.start_seconds
        return self


class CaptionResult(_StrictModel):
    summary: str = Field(description="An overview of the main content of the video")
    scenes: list[Scene] = Field(
        description="Scenes in chronological order; group continuous footage with a consistent setting into one scene"
    )

    @model_validator(mode="after")
    def scene_times(self) -> CaptionResult:
        previous = 0.0
        for scene in self.scenes:
            if scene.time_range.start_seconds < previous:
                raise ValueError("scenes must be in chronological order")
            previous = scene.time_range.start_seconds
        return self

    def check_duration(self, duration: float) -> None:
        """Check the known local duration without manufacturing or clamping model timestamps."""
        if duration > 0 and any(scene.time_range.end_seconds > duration + 1e-6 for scene in self.scenes):
            raise ValueError("scene end_seconds must not exceed the video duration")
