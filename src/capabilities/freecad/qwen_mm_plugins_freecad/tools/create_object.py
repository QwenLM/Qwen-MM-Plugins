"""MCP tool: create a new object in the running FreeCAD (text + optional screenshot)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class CreateObjectArgs(BaseModel):
    doc_name: str
    obj_type: str
    obj_name: str
    analysis_name: Optional[str] = None
    obj_properties: Optional[dict] = None


TOOL = {"name": "create_object", "args": CreateObjectArgs}


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Create a new object in FreeCAD.

    Object type starts with "Part::" or "Draft::" or "PartDesign::" or "Fem::".

    Args:
        doc_name: The name of the document to create the object in.
        obj_type: The type of the object to create (e.g. 'Part::Box', 'Part::Cylinder',
            'Draft::Circle', 'PartDesign::Body', etc.).
        obj_name: The name of the object to create.
        analysis_name: The name of the FEM analysis to add the object to (for Fem:: objects).
        obj_properties: The properties of the object to create.

    Examples:
        If you want to create a cylinder with a height of 30 and a radius of 10, you can use the following data.
        ```json
        {
            "doc_name": "MyCylinder",
            "obj_name": "Cylinder",
            "obj_type": "Part::Cylinder",
            "obj_properties": {
                "Height": 30,
                "Radius": 10,
                "Placement": {
                    "Base": {
                        "x": 10,
                        "y": 10,
                        "z": 0
                    },
                    "Rotation": {
                        "Axis": {
                            "x": 0,
                            "y": 0,
                            "z": 1
                        },
                        "Angle": 45
                    }
                },
                "ViewObject": {
                    "ShapeColor": [0.5, 0.5, 0.5, 1.0]
                }
            }
        }
        ```

        If you want to create a circle with a radius of 10, you can use the following data.
        ```json
        {
            "doc_name": "MyCircle",
            "obj_name": "Circle",
            "obj_type": "Draft::Circle",
        }
        ```

        If you want to create a FEM analysis, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMAnalysis",
            "obj_name": "FemAnalysis",
            "obj_type": "Fem::AnalysisPython",
        }
        ```

        If you want to create a FEM constraint, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMConstraint",
            "obj_name": "FemConstraint",
            "obj_type": "Fem::ConstraintFixed",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "References": [
                    {
                        "object_name": "MyObject",
                        "face": "Face1"
                    }
                ]
            }
        }
        ```

        If you want to create a FEM mechanical material, you can use the following data.
        ```json
        {
            "doc_name": "MyFEMAnalysis",
            "obj_name": "FemMechanicalMaterial",
            "obj_type": "Fem::MaterialCommon",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "Material": {
                    "Name": "MyMaterial",
                    "Density": "7900 kg/m^3",
                    "YoungModulus": "210 GPa",
                    "PoissonRatio": 0.3
                }
            }
        }
        ```

        If you want to create a FEM mesh, you can use the following data.
        The `Shape` property is required (legacy `Part` is also accepted).
        On FreeCAD 1.x the size limits are `CharacteristicLengthMax/Min`;
        the legacy `ElementSizeMax/Min` keys are also accepted.
        ```json
        {
            "doc_name": "MyFEMMesh",
            "obj_name": "FemMesh",
            "obj_type": "Fem::FemMeshGmsh",
            "analysis_name": "MyFEMAnalysis",
            "obj_properties": {
                "Shape": "MyObject",
                "CharacteristicLengthMax": 10,
                "CharacteristicLengthMin": 0.1
            }
        }
        ```
    """
    from qwen_mm_plugins_freecad._responses import add_screenshot_if_available, text_response
    from qwen_mm_plugins_freecad.loader import get_connection, only_text_feedback

    doc_name = arguments.get("doc_name")
    obj_type = arguments.get("obj_type")
    obj_name = arguments.get("obj_name")
    analysis_name = arguments.get("analysis_name")
    obj_properties = arguments.get("obj_properties")
    only_text = only_text_feedback()
    try:
        conn = get_connection()
        obj_data = {
            "Name": obj_name,
            "Type": obj_type,
            "Properties": obj_properties or {},
            "Analysis": analysis_name,
        }
        res = conn.create_object(doc_name, obj_data)
        if res.get("success"):
            response = text_response(f"Object '{res['object_name']}' created successfully")
        else:
            return text_response(f"Failed to create object: {res.get('error')}")
        screenshot = None if only_text else conn.get_active_screenshot()
        return add_screenshot_if_available(response, screenshot, only_text)
    except Exception as e:
        return text_response(f"Failed to create object: {e}")
