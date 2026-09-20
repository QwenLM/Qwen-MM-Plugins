"""MCP tool shim: plan_exploration — impl lives in `explore.py` (co-located with assess_coverage
so the shared heuristics don't duplicate). See explore.py docstring for details.
"""

from qwen_mm_plugins_video_spatio.tools.explore import TOOL_PLAN, handle_plan

# Re-export under the names the framework auto-discovery scans for (assignment, so ruff keeps them).
TOOL = TOOL_PLAN
handle = handle_plan
