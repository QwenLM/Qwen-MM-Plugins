"""MCP tool: apply a previously downloaded PolyHaven texture to an object (text return)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class SetTextureArgs(BaseModel):
    object_name: str
    texture_id: str


TOOL = {"name": "set_texture", "args": SetTextureArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Apply a previously downloaded Polyhaven texture to an object. Returns a message indicating
    success or failure.

    Args:
        object_name: Name of the object to apply the texture to.
        texture_id: ID of the Polyhaven texture to apply (must be downloaded first).
    """
    from qwen_mm_plugins_blender.loader import get_connection

    object_name = arguments.get("object_name", "")
    texture_id = arguments.get("texture_id", "")
    try:
        result = get_connection().send_command(
            "set_texture",
            {"object_name": object_name, "texture_id": texture_id},
        )

        if "error" in result:
            return [{"type": "text", "text": f"Error: {result['error']}"}]

        if result.get("success"):
            material_name = result.get("material", "")
            maps = ", ".join(result.get("maps", []))

            # Add detailed material info
            material_info = result.get("material_info", {})
            node_count = material_info.get("node_count", 0)
            has_nodes = material_info.get("has_nodes", False)
            texture_nodes = material_info.get("texture_nodes", [])

            output = f"Successfully applied texture '{texture_id}' to {object_name}.\n"
            output += f"Using material '{material_name}' with maps: {maps}.\n\n"
            output += f"Material has nodes: {has_nodes}\n"
            output += f"Total node count: {node_count}\n\n"

            if texture_nodes:
                output += "Texture nodes:\n"
                for node in texture_nodes:
                    output += f"- {node['name']} using image: {node['image']}\n"
                    if node["connections"]:
                        output += "  Connections:\n"
                        for conn in node["connections"]:
                            output += f"    {conn}\n"
            else:
                output += "No texture nodes found in the material.\n"

            return [{"type": "text", "text": output}]
        else:
            return [
                {
                    "type": "text",
                    "text": f"Failed to apply texture: {result.get('message', 'Unknown error')}",
                }
            ]
    except Exception as e:
        return [{"type": "text", "text": f"Error applying texture: {e}"}]
