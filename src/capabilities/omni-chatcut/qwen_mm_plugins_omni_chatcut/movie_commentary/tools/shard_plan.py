"""Freeze a validated movie-commentary plan into immutable executor shards."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from shared.content import text, text_error

from ..contracts import shard_plan


class ShardPlanArgs(BaseModel):
    project_dir: str
    plan_path: str | None = None
    segments_per_shard: int = Field(default=10, ge=1, le=20)
    overwrite: bool = False


TOOL = {"name": "shard_movie_commentary_plan", "args": ShardPlanArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate a movie-commentary plan and copy consecutive segments unchanged into immutable shard manifests.

    Args:
        project_dir: Movie-commentary project root.
        plan_path: Plan JSON; defaults to plan/editing_plan.json.
        segments_per_shard: Consecutive plan segments per shard.
        overwrite: Replace existing shard manifests.
    """
    try:
        return [text(json.dumps(shard_plan(**arguments), ensure_ascii=False))]
    except Exception as exc:  # noqa: BLE001
        return text_error(str(exc))
