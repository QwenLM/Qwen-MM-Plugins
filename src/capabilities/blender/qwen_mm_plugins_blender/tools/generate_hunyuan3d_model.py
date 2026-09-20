"""MCP tool: generate a 3D asset with Hunyuan3D and import it into Blender."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class GenerateHunyuan3DModelArgs(BaseModel):
    text_prompt: Optional[str] = None
    input_image_url: Optional[str] = None


TOOL = {"name": "generate_hunyuan3d_model", "args": GenerateHunyuan3DModelArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate 3D asset using Hunyuan3D by providing either text description, image reference, or both
    for the desired asset, and import the asset into Blender. The 3D asset has built-in materials.
    Parameters: text_prompt (optional) a short description of the desired model in English/Chinese;
    input_image_url (optional) the local or remote url of the input image, accepts None if only
    using text prompt. When successful, returns a JSON with job_id (format: "job_xxx") indicating
    the task is in progress; when the job completes, the status will change to "DONE" indicating the
    model has been imported; returns error message if the operation fails.

    Args:
        text_prompt: (Optional) A short description of the desired model in English/Chinese.
        input_image_url: (Optional) The local or remote url of the input image. Accepts None if only
            using text prompt.
    """
    import json

    from qwen_mm_plugins_blender.loader import get_connection

    text_prompt = arguments.get("text_prompt")
    input_image_url = arguments.get("input_image_url")
    try:
        result = get_connection().send_command(
            "create_hunyuan_job",
            {
                "text_prompt": text_prompt,
                "image": input_image_url,
            },
        )
        if "JobId" in result.get("Response", {}):
            job_id = result["Response"]["JobId"]
            formatted_job_id = f"job_{job_id}"
            return [{"type": "text", "text": json.dumps({"job_id": formatted_job_id})}]
        return [{"type": "text", "text": json.dumps(result)}]
    except Exception as e:
        return [{"type": "text", "text": f"Error generating Hunyuan3D task: {str(e)}"}]
