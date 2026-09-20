"""MCP tool: poll the status of a Hyper3D Rodin generation task."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class PollRodinJobStatusArgs(BaseModel):
    subscription_key: Optional[str] = None
    request_id: Optional[str] = None


TOOL = {"name": "poll_rodin_job_status", "args": PollRodinJobStatusArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Check if the Hyper3D Rodin generation task is completed. For MAIN_SITE mode, pass
    subscription_key: returns a list of status, done when all are "Done" ("Failed" means the
    generation failed). For FAL_AI mode, pass request_id: returns the generation task status, done
    when "COMPLETED" and in progress when "IN_PROGRESS". This is a polling API, so only proceed once
    the status is finally determined.

    Args:
        subscription_key: For Hyper3D Rodin mode MAIN_SITE: the subscription_key given in the
            generate model step.
        request_id: For Hyper3D Rodin mode FAL_AI: the request_id given in the generate model step.
    """
    import json

    from qwen_mm_plugins_blender.loader import get_connection

    subscription_key = arguments.get("subscription_key", None)
    request_id = arguments.get("request_id", None)
    try:
        kwargs = {}
        if subscription_key:
            kwargs = {"subscription_key": subscription_key}
        elif request_id:
            kwargs = {"request_id": request_id}
        result = get_connection().send_command("poll_rodin_job_status", kwargs)
        return [{"type": "text", "text": json.dumps(result, indent=2)}]
    except Exception as e:
        return [{"type": "text", "text": f"Error generating Hyper3D task: {e}"}]
