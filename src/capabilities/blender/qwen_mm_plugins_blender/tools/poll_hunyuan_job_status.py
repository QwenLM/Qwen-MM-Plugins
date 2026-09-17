"""MCP tool: poll the status of a Hunyuan3D generation job."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class PollHunyuanJobStatusArgs(BaseModel):
    job_id: Optional[str] = None


TOOL = {"name": "poll_hunyuan_job_status", "args": PollHunyuanJobStatusArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Check if the Hunyuan3D generation task is completed. Parameters: job_id, the job_id given in the
    generate model step. Returns the generation task status; the task is done if status is "DONE"
    and in progress if status is "RUN". If status is "DONE", the response includes a field named
    ResultFile3Ds that contains the generated ZIP file path of the 3D model in OBJ format. This is a
    polling API, so only proceed once the status is finally determined ("DONE" or some failed
    state).

    Args:
        job_id: The job_id given in the generate model step.
    """
    import json

    from qwen_mm_plugins_blender.loader import get_connection

    job_id = arguments.get("job_id")
    try:
        kwargs = {
            "job_id": job_id,
        }
        result = get_connection().send_command("poll_hunyuan_job_status", kwargs)
        return [{"type": "text", "text": json.dumps(result, indent=2)}]
    except Exception as e:
        return [{"type": "text", "text": f"Error generating Hunyuan3D task: {str(e)}"}]
