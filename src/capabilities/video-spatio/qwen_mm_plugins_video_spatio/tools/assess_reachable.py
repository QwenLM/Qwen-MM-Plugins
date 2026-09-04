"""MCP tool shim: assess_reachable — impl lives in `explore.py` (co-located with assess_coverage
and plan_exploration so the shared geometry/label heuristics don't duplicate). See explore.py
docstring for details.
"""

from qwen_mm_plugins_video_spatio.tools.explore import TOOL_REACH, handle_reach

# Re-export under the names the framework auto-discovery scans for (assignment, so ruff keeps them).
TOOL = TOOL_REACH
handle = handle_reach
