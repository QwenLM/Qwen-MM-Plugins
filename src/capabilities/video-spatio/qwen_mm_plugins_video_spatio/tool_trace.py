"""Structured tool execution tracing for failure attribution and analysis."""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ToolTrace:
    """A single tool invocation record."""

    step: int
    tool: str
    method: str
    args_summary: str
    result_summary: str
    duration_ms: float
    success: bool
    error_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ToolTracer:
    """Collects structured traces across a session.

    Attached to ToolsModule and injected into GPU tools. Each tool call
    records timing, success/failure, and a brief summary of args and results.
    """

    def __init__(self):
        self._traces: List[ToolTrace] = []
        self._current_step: int = 0

    def set_step(self, step: int):
        self._current_step = step

    def record(self, trace: ToolTrace):
        self._traces.append(trace)

    def get_step_traces(self, step: int) -> List[ToolTrace]:
        return [t for t in self._traces if t.step == step]

    def get_failure_traces(self) -> List[ToolTrace]:
        return [t for t in self._traces if not t.success]

    def build_failure_attribution(self, step: int) -> str:
        """Generate a failure attribution string for the given step.

        Returns empty string if no tool traces exist for this step.
        """
        step_traces = self.get_step_traces(step)
        if not step_traces:
            return ""

        failed = [t for t in step_traces if not t.success]
        if not failed:
            return ""

        lines = ["[TOOL FAILURE ATTRIBUTION]"]
        for t in failed:
            lines.append(f"  {t.tool}.{t.method}({t.args_summary}) -> FAILED ({t.error_type}): {t.result_summary}")

        succeeded = [t for t in step_traces if t.success]
        if succeeded:
            lines.append(f"  ({len(succeeded)} other tool call(s) succeeded)")

        return "\n".join(lines)

    def summary(self) -> str:
        if not self._traces:
            return "Tool traces: empty"

        total = len(self._traces)
        failed = sum(1 for t in self._traces if not t.success)
        by_tool: Dict[str, int] = {}
        total_ms = 0.0
        for t in self._traces:
            by_tool[t.tool] = by_tool.get(t.tool, 0) + 1
            total_ms += t.duration_ms

        tool_str = ", ".join(f"{k}:{v}" for k, v in sorted(by_tool.items()))
        lines = [
            f"Tool traces: {total} calls ({failed} failed), total {total_ms:.0f}ms",
            f"  By tool: {tool_str}",
        ]

        if failed > 0:
            lines.append("  Failures:")
            for t in self._traces:
                if not t.success:
                    lines.append(f"    step {t.step}: {t.tool}.{t.method} -> {t.error_type}: {t.result_summary[:100]}")

        return "\n".join(lines)

    def to_list(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self._traces]
