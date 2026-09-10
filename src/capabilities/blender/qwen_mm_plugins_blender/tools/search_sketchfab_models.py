"""MCP tool: search for models on Sketchfab with optional filtering (text return)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class SearchSketchfabModelsArgs(BaseModel):
    query: str
    categories: Optional[str] = None
    count: int = 20
    downloadable: bool = True


TOOL = {"name": "search_sketchfab_models", "args": SearchSketchfabModelsArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Search for models on Sketchfab with optional filtering.

    Returns a formatted list of matching models.

    Args:
        query: Text to search for.
        categories: Optional comma-separated list of categories.
        count: Maximum number of results to return (default 20).
        downloadable: Whether to include only downloadable models (default True).
    """
    from qwen_mm_plugins_blender.loader import get_connection

    query = arguments.get("query", "")
    categories = arguments.get("categories", None)
    count = arguments.get("count", 20)
    downloadable = arguments.get("downloadable", True)
    try:
        result = get_connection().send_command(
            "search_sketchfab_models",
            {
                "query": query,
                "categories": categories,
                "count": count,
                "downloadable": downloadable,
            },
        )

        # Safely handle a None/absent response before indexing into it.
        if result is None:
            return [{"type": "text", "text": "Error: Received no response from Sketchfab search"}]

        if "error" in result:
            return [{"type": "text", "text": f"Error: {result['error']}"}]

        # Format the results
        models = result.get("results", []) or []
        if not models:
            return [{"type": "text", "text": f"No models found matching '{query}'"}]

        formatted_output = f"Found {len(models)} models matching '{query}':\n\n"

        for model in models:
            if model is None:
                continue

            model_name = model.get("name", "Unnamed model")
            model_uid = model.get("uid", "Unknown ID")
            formatted_output += f"- {model_name} (UID: {model_uid})\n"

            # Get user info with safety checks
            user = model.get("user") or {}
            username = user.get("username", "Unknown author") if isinstance(user, dict) else "Unknown author"
            formatted_output += f"  Author: {username}\n"

            # Get license info with safety checks
            license_data = model.get("license") or {}
            license_label = license_data.get("label", "Unknown") if isinstance(license_data, dict) else "Unknown"
            formatted_output += f"  License: {license_label}\n"

            # Add face count and downloadable status
            face_count = model.get("faceCount", "Unknown")
            is_downloadable = "Yes" if model.get("isDownloadable") else "No"
            formatted_output += f"  Face count: {face_count}\n"
            formatted_output += f"  Downloadable: {is_downloadable}\n\n"

        return [{"type": "text", "text": formatted_output}]
    except Exception as e:
        return [{"type": "text", "text": f"Error searching Sketchfab models: {e}"}]
