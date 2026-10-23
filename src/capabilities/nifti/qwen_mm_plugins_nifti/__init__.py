"""Header inspection and configurable NIfTI slices as an independent MCP capability."""

from mcp_framework import build_registry

__version__ = "1.1.0"

SPECS, get_handler, list_tools = build_registry(__name__, ["tools"])

SYSTEM_DEPS: list[dict] = []

USAGE_NOTE = (
    "Use nifti_inspect for header-only metadata of local .nii/.nii.gz volumes, or call "
    "nifti_render_slices directly for configurable source-axis slices, shared volume-level "
    "intensity mapping, and effective-configuration reporting. "
    "Spatial units default to mm only when the NIfTI header leaves them unknown."
)
