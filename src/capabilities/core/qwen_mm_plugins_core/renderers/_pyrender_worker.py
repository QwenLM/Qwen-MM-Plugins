"""Run the pyrender backend in an isolated Python process."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PACKAGE_DIR.parent))

# The installed wheel already exposes mcp_framework through site-packages. A
# source checkout keeps it at src/, so add that root when this helper is run by
# its file path.
_SOURCE_ROOT = _PACKAGE_DIR.parents[2]
if (_SOURCE_ROOT / "mcp_framework.py").is_file():
    sys.path.insert(0, str(_SOURCE_ROOT))


def main() -> int:
    from qwen_mm_plugins_core.renderers import model3d

    if len(sys.argv) < 3:
        print("Usage: _pyrender_worker <model> <outdir> [max_views] [budget]", file=sys.stderr)
        return 1

    path = sys.argv[1]
    output_dir = sys.argv[2]
    max_pages = int(sys.argv[3]) if len(sys.argv) > 3 else len(model3d.VIEWS)
    budget = sys.argv[4] if len(sys.argv) > 4 else None

    try:
        os.makedirs(output_dir, exist_ok=True)
        meshes, total_verts, total_faces = model3d._load_scene(path)
        try:
            images = model3d._render_pyrender(
                meshes,
                path,
                model3d._has_real_materials(meshes),
                total_verts,
                total_faces,
                max_pages,
            )
        except Exception as exc:
            print(f"pyrender failed ({exc}); falling back to matplotlib", file=sys.stderr)
            images = model3d._render_matplotlib(meshes, path, total_verts, total_faces, max_pages)

        for index, image in enumerate(images):
            output_path = os.path.join(output_dir, f"view_{index}.png")
            image.save(output_path)

        if budget is not None:
            content = model3d._images_to_content(images, path, budget)
            with open(os.path.join(output_dir, "result.json"), "w", encoding="utf-8") as result_file:
                json.dump(content, result_file)

        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
