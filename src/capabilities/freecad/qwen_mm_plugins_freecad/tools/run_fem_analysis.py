"""MCP tool: run the CalculiX FEM solver on an existing FreeCAD analysis container."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RunFemAnalysisArgs(BaseModel):
    doc_name: str
    analysis_name: str
    timeout: int = 600


TOOL = {"name": "run_fem_analysis", "args": RunFemAnalysisArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Run the CalculiX solver on an existing Fem::FemAnalysis container and return summary results.

    Prerequisites in the document: - A Part-derived solid (e.g. Part::Box, PartDesign::Body) acting
    as the geometry. - A Fem::AnalysisPython container created via `create_object`. - A
    Fem::MaterialCommon assigned to the geometry, added to the analysis. - A Fem::FemMeshGmsh
    referencing the geometry, added to the analysis (the mesh is generated automatically when
    created via `create_object`). - At least one Fem::ConstraintFixed and one Fem::ConstraintForce
    (or ConstraintPressure) bound to faces of the geometry, added to the analysis.

    A SolverCcxTools is auto-created if the analysis has none.

    The solver runs synchronously on the FreeCAD GUI thread and blocks all other RPC calls for its
    duration; do not fan out parallel requests.

    Returns max von Mises stress (MPa), max/min displacement (mm), node count, and the working
    directory CalculiX wrote to. On failure, returns the prerequisite-check or solver error along
    with the working directory for triage.

    Args:
        doc_name: Name of the FreeCAD document.
        analysis_name: Name of the Fem::AnalysisPython object.
        timeout: Seconds to wait for the solver (default 600).
    """
    from qwen_mm_plugins_freecad._responses import add_screenshot_if_available, json_response, text_response
    from qwen_mm_plugins_freecad.loader import get_connection, only_text_feedback

    doc_name = arguments.get("doc_name", "")
    analysis_name = arguments.get("analysis_name", "")
    timeout = arguments.get("timeout", 600)
    only_text = only_text_feedback()
    try:
        conn = get_connection()
        res = conn.run_fem_analysis(doc_name, analysis_name, timeout)
        if res.get("success"):

            def fmt(v, unit):
                return f"{v:.4g} {unit}" if isinstance(v, (int, float)) else f"unavailable ({unit})"

            screenshot = conn.get_active_screenshot() if not only_text else None
            response = json_response(
                {
                    "summary": (
                        f"FEM analysis '{analysis_name}' solved. "
                        f"max von Mises = {fmt(res.get('max_von_mises_MPa'), 'MPa')}, "
                        f"max displacement = {fmt(res.get('max_displacement_mm'), 'mm')} "
                        f"({res.get('node_count')} nodes)."
                    ),
                    **res,
                }
            )
            return add_screenshot_if_available(response, screenshot, only_text)
        return json_response(
            {
                "summary": f"FEM analysis '{analysis_name}' failed: {res.get('error')}",
                **res,
            }
        )
    except Exception as e:
        return text_response(f"Failed to run FEM analysis: {e}")
