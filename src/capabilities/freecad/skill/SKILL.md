---
name: qwen-mm-plugins-freecad
description: Use whenever a task involves parametric CAD in FreeCAD — modeling parts and assemblies, editing object properties, technical drawings, importing/exporting STEP/STL/OBJ/DXF, PDF/Excel reports from a model, or finite-element (FEM/CalculiX) analysis. Requires a running FreeCAD instance with the FreeCADMCP addon (see Prerequisite).
---

# FreeCAD Parametric Modeling

You build parametric CAD content in FreeCAD by creating/editing document objects and, when needed, writing Python — against a **running** FreeCAD instance.

## Prerequisite: a live FreeCAD + addon (started for you on first use)

These tools are a **thin client**: they talk XML-RPC to a **running** FreeCAD carrying the FreeCADMCP
addon (bundled in this plugin), and do **not** launch FreeCAD while serving. Normally you start
nothing — just call a tool.

- **Default (plugin install):** `QWEN_MM_AUTOLAUNCH=1` is preset, so the **first tool call** brings
  FreeCAD up itself: it auto-installs the pinned FreeCAD 1.1.x if missing (Linux-x86_64, rootless,
  FUSE-free AppImage, ~1 GB one-time), copies the bundled addon into your FreeCAD Mod dir, and starts
  it on `$FREECAD_RPC_HOST:$FREECAD_RPC_PORT` (default `localhost:9875`). Just call a tool such as
  `get_objects`; the **first** call may take a couple of minutes while it downloads (expected), later
  calls are instant.
- **Don't shell out to `qwen-mm-plugins-freecad --launch-app` under a plugin install** — that console
  entry lives inside the uvx environment, not your shell PATH (`command not found`). Use it only from
  a source checkout, or for a manual / GUI start:
  ```bash
  python3 src/capabilities/freecad/qwen_mm_plugins_freecad --launch-app        # headless (xvfb)
  python3 src/capabilities/freecad/qwen_mm_plugins_freecad --launch-app --gui  # real display
  ```

Auto-launch can't cover a few things: (1) auto-download is **Linux-x86_64 only** — elsewhere install
FreeCAD yourself (`apt install freecad`, or extract an AppImage and set `FREECAD_BINARY=<AppRun>`);
(2) a headless box needs a virtual display (`apt install xvfb`, needs root); (3) FEM needs the
CalculiX solver (`ccx`) on PATH. If a tool reports it can't connect, it's almost always one of the
first two — the error message spells out which; from a checkout, `... --check-system` lists every
missing system tool. Set `FREECAD_ONLY_TEXT_FEEDBACK=1` to drop the screenshot most tools attach.

## Asset creation strategy

When creating content in FreeCAD, follow these steps:

0. Before starting any task, always use `get_objects` to confirm the current state of the document (and `list_documents` / `create_document` as needed).
1. **Utilize the parts library**: check available parts with `get_parts_list`; if the required part exists, use `insert_part_from_library` to insert it.
2. **If the part isn't in the library**: create basic shapes (`Part::Box`, `Part::Cylinder`, `Part::Sphere`, `Draft::*`, `PartDesign::*`, …) with `create_object`, then refine detailed properties with `edit_object`.
3. Always assign clear, descriptive names to objects.
4. Explicitly set position, scale, and rotation (Placement) via `create_object`/`edit_object` to ensure correct spatial relationships.
5. After editing an object, **verify** the properties actually applied using `get_object`.
6. For detailed customization or specialized operations, use `execute_code` to run custom Python.

Only fall back to basic creation methods when: the asset isn't in the parts library, a basic shape is explicitly requested, or a complex shape requires custom scripting.

## execute_code vs execute_code_async

- **`execute_code`** runs on FreeCAD's GUI thread — the safe default for anything that touches documents, document objects, `FreeCADGui`, the active view, selection, `recompute()`, or save.
- **`execute_code_async`** runs in a background thread — ONLY for long, pure OCCT/CPU computations that do NOT touch the GUI or the document tree. Pattern: fetch shapes with `execute_code`, stash intermediates in a module-level Python variable, run the heavy compute async, then apply results back via `execute_code`.

## Visual verification

Use `get_view` (Isometric/Front/Top/…) to inspect geometry, and re-check with `get_object` after edits. Most mutating tools already return a screenshot unless `FREECAD_ONLY_TEXT_FEEDBACK` is set.

## FEM (finite-element) analysis

`run_fem_analysis` runs the CalculiX solver on a `Fem::AnalysisPython` container. Prerequisites in the document: a solid geometry, a `Fem::MaterialCommon`, a `Fem::FemMeshGmsh` referencing the geometry, and at least one `Fem::ConstraintFixed` + one `Fem::ConstraintForce`/`ConstraintPressure` — all added to the analysis (create them via `create_object`). CalculiX (`ccx`) must be installed. Returns max von Mises stress, max/min displacement, and node count.

## Output files

Save the final `.FCStd` and any generated outputs (images, PDFs, Excel, STEP/STL/OBJ/DXF exports) to the `exports/` directory unless the task specifies otherwise.
