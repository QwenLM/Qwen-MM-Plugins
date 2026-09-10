"""MCP tool: download and import a PolyHaven asset into Blender (text return)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class DownloadPolyhavenAssetArgs(BaseModel):
    asset_id: str
    asset_type: str
    resolution: str = "1k"
    file_format: Optional[str] = None


TOOL = {"name": "download_polyhaven_asset", "args": DownloadPolyhavenAssetArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Download and import a Polyhaven asset into Blender. Returns a message indicating success or
    failure.

    Args:
        asset_id: The ID of the asset to download.
        asset_type: The type of asset (hdris, textures, models).
        resolution: The resolution to download (e.g., 1k, 2k, 4k).
        file_format: Optional file format (e.g., hdr, exr for HDRIs; jpg, png for textures; gltf,
            fbx for models).
    """
    from qwen_mm_plugins_blender.loader import get_connection

    asset_id = arguments.get("asset_id", "")
    asset_type = arguments.get("asset_type", "")
    resolution = arguments.get("resolution", "1k")
    file_format = arguments.get("file_format", None)
    try:
        result = get_connection().send_command(
            "download_polyhaven_asset",
            {
                "asset_id": asset_id,
                "asset_type": asset_type,
                "resolution": resolution,
                "file_format": file_format,
            },
        )

        if "error" in result:
            return [{"type": "text", "text": f"Error: {result['error']}"}]

        if result.get("success"):
            message = result.get("message", "Asset downloaded and imported successfully")

            # Add additional information based on asset type
            if asset_type == "hdris":
                text = f"{message}. The HDRI has been set as the world environment."
            elif asset_type == "textures":
                material_name = result.get("material", "")
                maps = ", ".join(result.get("maps", []))
                text = f"{message}. Created material '{material_name}' with maps: {maps}."
            elif asset_type == "models":
                text = f"{message}. The model has been imported into the current scene."
            else:
                text = message
            return [{"type": "text", "text": text}]
        else:
            return [
                {
                    "type": "text",
                    "text": f"Failed to download asset: {result.get('message', 'Unknown error')}",
                }
            ]
    except Exception as e:
        return [{"type": "text", "text": f"Error downloading Polyhaven asset: {e}"}]
