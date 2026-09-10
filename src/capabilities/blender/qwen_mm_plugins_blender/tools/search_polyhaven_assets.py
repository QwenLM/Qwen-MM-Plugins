"""MCP tool: search PolyHaven assets with optional category filtering (text return)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class SearchPolyhavenAssetsArgs(BaseModel):
    asset_type: str = "all"
    categories: Optional[str] = None


TOOL = {"name": "search_polyhaven_assets", "args": SearchPolyhavenAssetsArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Search for assets on Polyhaven with optional filtering. Returns a list of matching assets with
    basic information.

    Args:
        asset_type: Type of assets to search for (hdris, textures, models, all).
        categories: Optional comma-separated list of categories to filter by.
    """
    from qwen_mm_plugins_blender.loader import get_connection

    asset_type = arguments.get("asset_type", "all")
    categories = arguments.get("categories", None)
    try:
        result = get_connection().send_command(
            "search_polyhaven_assets",
            {"asset_type": asset_type, "categories": categories},
        )

        if "error" in result:
            return [{"type": "text", "text": f"Error: {result['error']}"}]

        # Format the assets in a more readable way
        assets = result["assets"]
        total_count = result["total_count"]
        returned_count = result["returned_count"]

        formatted_output = f"Found {total_count} assets"
        if categories:
            formatted_output += f" in categories: {categories}"
        formatted_output += f"\nShowing {returned_count} assets:\n\n"

        # Sort assets by download count (popularity)
        sorted_assets = sorted(assets.items(), key=lambda x: x[1].get("download_count", 0), reverse=True)

        for asset_id, asset_data in sorted_assets:
            formatted_output += f"- {asset_data.get('name', asset_id)} (ID: {asset_id})\n"
            formatted_output += f"  Type: {['HDRI', 'Texture', 'Model'][asset_data.get('type', 0)]}\n"
            formatted_output += f"  Categories: {', '.join(asset_data.get('categories', []))}\n"
            formatted_output += f"  Downloads: {asset_data.get('download_count', 'Unknown')}\n\n"

        return [{"type": "text", "text": formatted_output}]
    except Exception as e:
        return [{"type": "text", "text": f"Error searching Polyhaven assets: {e}"}]
