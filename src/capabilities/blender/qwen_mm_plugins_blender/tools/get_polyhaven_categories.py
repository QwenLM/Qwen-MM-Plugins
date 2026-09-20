"""MCP tool: list categories for a specific PolyHaven asset type (text return)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PolyhavenCategoriesArgs(BaseModel):
    asset_type: str = "hdris"


TOOL = {"name": "get_polyhaven_categories", "args": PolyhavenCategoriesArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Get a list of categories for a specific asset type on Polyhaven.

    Args:
        asset_type: The type of asset to get categories for (hdris, textures, models, all).
    """
    from qwen_mm_plugins_blender.loader import get_connection, refresh_polyhaven

    asset_type = arguments.get("asset_type", "hdris")
    try:
        if not refresh_polyhaven():
            return [
                {
                    "type": "text",
                    "text": "PolyHaven integration is disabled. Select it in the sidebar in BlenderMCP, then run it again.",
                }
            ]
        result = get_connection().send_command("get_polyhaven_categories", {"asset_type": asset_type})

        if "error" in result:
            return [{"type": "text", "text": f"Error: {result['error']}"}]

        # Format the categories in a more readable way
        categories = result["categories"]
        formatted_output = f"Categories for {asset_type}:\n\n"

        # Sort categories by count (descending)
        sorted_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)

        for category, count in sorted_categories:
            formatted_output += f"- {category}: {count} assets\n"

        return [{"type": "text", "text": formatted_output}]
    except Exception as e:
        return [{"type": "text", "text": f"Error getting Polyhaven categories: {e}"}]
