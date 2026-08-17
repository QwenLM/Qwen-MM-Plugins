"""Render 3D model files (OBJ, STL, GLB, GLTF, FBX, PLY, STEP/STP).

Backends: Blender Cycles -> pyrender EGL -> matplotlib.
"""

from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from qwen_mm_plugins_core.renderers import DEFAULT_MAX_PAGES

log = logging.getLogger(__name__)

_egl_configured = False

VIEWS = [
    ("Perspective", 30, 45),
    ("Front", 0, 0),
    ("Top", 90, 0),
]


def _ensure_egl():
    global _egl_configured
    if not _egl_configured:
        if sys.platform == "linux":
            os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
        _egl_configured = True


def _render_pyrender_subprocess(path: str, max_pages: int) -> list:
    """Render with pyrender in a fresh interpreter and load its output images."""
    from PIL import Image

    tmp_dir = tempfile.mkdtemp(prefix="pyrender_render_")
    worker = os.path.join(os.path.dirname(__file__), "_pyrender_worker.py")

    try:
        result = subprocess.run(
            [
                sys.executable,
                worker,
                os.path.abspath(path),
                tmp_dir,
                str(max_pages),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            error_tail = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(f"pyrender subprocess exited with code {result.returncode}: {error_tail}")

        images = []
        for index in range(len(VIEWS[:max_pages])):
            output_path = os.path.join(tmp_dir, f"view_{index}.png")
            with Image.open(output_path) as image:
                images.append(image.copy())
        return images
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _load_scene(path: str):
    """Load a 3D file and return (meshes, total_verts, total_faces)."""
    import trimesh

    scene = trimesh.load(path)
    meshes = list(scene.geometry.values()) if isinstance(scene, trimesh.Scene) else [scene]
    meshes = [m for m in meshes if isinstance(m, trimesh.Trimesh) and len(m.faces) > 0]

    if not meshes:
        raise ValueError("No renderable mesh found in file")

    total_verts = sum(len(m.vertices) for m in meshes)
    total_faces = sum(len(m.faces) for m in meshes)
    return meshes, total_verts, total_faces


def _has_real_materials(meshes) -> bool:
    for m in meshes:
        vis = getattr(m, "visual", None)
        if vis is None:
            continue
        vtype = type(vis).__name__
        if vtype == "TextureVisuals":
            return True
        if vtype == "ColorVisuals":
            fc = getattr(vis, "face_colors", None)
            if fc is not None and len(fc) == len(m.faces):
                unique = set(map(tuple, fc[:100]))
                if len(unique) > 1:
                    return True
    return False


def _default_material():
    import pyrender

    return pyrender.MetallicRoughnessMaterial(
        baseColorFactor=[0.7, 0.75, 0.8, 1.0],
        metallicFactor=0.3,
        roughnessFactor=0.6,
    )


def _camera_pose(elev_deg: float, azim_deg: float, distance: float, center):
    import numpy as np

    elev = np.radians(elev_deg)
    azim = np.radians(azim_deg)

    x = distance * np.cos(elev) * np.sin(azim)
    y = distance * np.sin(elev)
    z = distance * np.cos(elev) * np.cos(azim)

    cam_pos = center + np.array([x, y, z])

    forward = center - cam_pos
    norm = np.linalg.norm(forward)
    if norm < 1e-9:
        forward = np.array([0.0, 0.0, -1.0])
    else:
        forward = forward / norm

    world_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, world_up)
    if np.linalg.norm(right) < 1e-6:
        world_up = np.array([0.0, 0.0, -1.0])
        right = np.cross(forward, world_up)
    right = right / np.linalg.norm(right)

    up = np.cross(right, forward)
    up = up / np.linalg.norm(up)

    # pyrender: camera looks along -Z
    pose = np.eye(4)
    pose[:3, 0] = right
    pose[:3, 1] = up
    pose[:3, 2] = -forward
    pose[:3, 3] = cam_pos
    return pose


def _light_pose(direction):
    """Create a pose matrix for a directional light."""
    import numpy as np

    d = np.array(direction, dtype=np.float64)
    d = d / np.linalg.norm(d)

    forward = -d
    world_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, world_up)
    if np.linalg.norm(right) < 1e-6:
        world_up = np.array([0.0, 0.0, 1.0])
        right = np.cross(forward, world_up)
    right = right / np.linalg.norm(right)
    up = np.cross(right, forward)
    up = up / np.linalg.norm(up)

    pose = np.eye(4)
    pose[:3, 0] = right
    pose[:3, 1] = up
    pose[:3, 2] = -forward
    return pose


def _add_overlay(image, path: str, title: str, total_verts: int, total_faces: int):
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image)
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        font_info = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    except (OSError, IOError):
        font_title = ImageFont.load_default()
        font_info = font_title

    header = f"{os.path.basename(path)} — {title}"
    info = f"{total_verts:,} vertices, {total_faces:,} faces"

    draw.text((13, 13), header, fill="#aaaaaa", font=font_title)
    draw.text((12, 12), header, fill="#333333", font=font_title)

    draw.text((13, image.height - 29), info, fill="#aaaaaa", font=font_info)
    draw.text((12, image.height - 30), info, fill="#666666", font=font_info)


def _build_pyrender_scene(meshes, has_materials):
    import numpy as np
    import pyrender

    scene = pyrender.Scene(
        bg_color=[1.0, 1.0, 1.0, 1.0],
        ambient_light=[0.15, 0.15, 0.15],
    )

    default_mat = None if has_materials else _default_material()

    all_mins = []
    all_maxs = []

    for m in meshes:
        # TextureVisuals → ColorVisuals to avoid glGenTextures EGL bug
        vis = getattr(m, "visual", None)
        if vis is not None and type(vis).__name__ == "TextureVisuals":
            try:
                m.visual = m.visual.to_color()
            except Exception:
                pass

        kwargs = {}
        if not has_materials:
            kwargs["material"] = default_mat
        try:
            pr_mesh = pyrender.Mesh.from_trimesh(m, **kwargs)
        except Exception:
            pr_mesh = pyrender.Mesh.from_trimesh(m, material=_default_material())
        scene.add(pr_mesh)
        all_mins.append(m.vertices.min(axis=0))
        all_maxs.append(m.vertices.max(axis=0))

    bbox_min = np.min(all_mins, axis=0)
    bbox_max = np.max(all_maxs, axis=0)
    center = (bbox_min + bbox_max) / 2.0
    scale = np.linalg.norm(bbox_max - bbox_min)

    # Three-point lighting
    key = pyrender.DirectionalLight(color=[1.0, 1.0, 1.0], intensity=5.0)
    scene.add(key, pose=_light_pose([1.0, -1.0, -1.0]))

    fill = pyrender.DirectionalLight(color=[0.9, 0.9, 1.0], intensity=2.5)
    scene.add(fill, pose=_light_pose([-1.0, -0.5, -0.8]))

    rim = pyrender.DirectionalLight(color=[1.0, 1.0, 0.95], intensity=2.0)
    scene.add(rim, pose=_light_pose([0.0, -0.3, 1.0]))

    return scene, center, scale


def _render_pyrender(meshes, path, has_materials, total_verts, total_faces, max_pages):
    _ensure_egl()
    import numpy as np
    import pyrender
    from PIL import Image

    scene, center, scale = _build_pyrender_scene(meshes, has_materials)

    camera = pyrender.PerspectiveCamera(yfov=np.pi / 4.0)
    cam_node = scene.add(camera)

    distance = max(scale * 1.8, 0.1)
    viewport_size = 1200

    renderer = pyrender.OffscreenRenderer(viewport_size, viewport_size)
    try:
        images = []
        for title, elev, azim in VIEWS[:max_pages]:
            pose = _camera_pose(elev, azim, distance, center)
            scene.set_pose(cam_node, pose)
            color, _ = renderer.render(scene)
            img = Image.fromarray(color)
            _add_overlay(img, path, title, total_verts, total_faces)
            images.append(img)
        return images
    finally:
        renderer.delete()


# matplotlib fallback


def _sample_face_colors(mesh, np):
    """Sample texture color at each face's UV centroid. Returns Nx4 RGBA or None."""
    vis = getattr(mesh, "visual", None)
    if vis is None:
        return None

    if type(vis).__name__ == "TextureVisuals":
        uv = getattr(vis, "uv", None)
        mat = getattr(vis, "material", None)
        if uv is None or mat is None:
            return None
        tex_img = getattr(mat, "baseColorTexture", None) or getattr(mat, "image", None)
        if tex_img is None:
            return None
        tex = np.array(tex_img)
        h, w = tex.shape[:2]
        colors = np.zeros((len(mesh.faces), 4), dtype=np.float64)
        for i, face in enumerate(mesh.faces):
            centroid_uv = uv[face].mean(axis=0)
            px = int(np.clip(centroid_uv[0] * (w - 1), 0, w - 1))
            py = int(np.clip((1.0 - centroid_uv[1]) * (h - 1), 0, h - 1))
            pixel = tex[py, px]
            colors[i, :3] = pixel[:3] / 255.0
            colors[i, 3] = pixel[3] / 255.0 if len(pixel) > 3 else 1.0
        return colors

    if type(vis).__name__ == "ColorVisuals":
        fc = getattr(vis, "face_colors", None)
        if fc is not None and len(fc) == len(mesh.faces):
            return fc.astype(np.float64) / 255.0

    return None


def _render_matplotlib(meshes, path, total_verts, total_faces, max_pages):
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt
    import numpy as np
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from PIL import Image

    all_verts = []
    all_faces = []
    all_colors = []
    offset = 0

    for m in meshes:
        fc = _sample_face_colors(m, np)
        all_verts.append(m.vertices)
        all_faces.append(m.faces + offset)
        if fc is not None:
            all_colors.append(fc)
        else:
            default = np.full((len(m.faces), 4), [0.357, 0.608, 0.835, 0.85])
            all_colors.append(default)
        offset += len(m.vertices)

    vertices = np.vstack(all_verts)
    faces = np.vstack(all_faces)
    face_colors = np.vstack(all_colors)
    has_texture = any(_sample_face_colors(m, np) is not None for m in meshes)

    MAX_MPL_FACES = 80_000
    if len(faces) > MAX_MPL_FACES:
        idx = np.random.default_rng(42).choice(len(faces), MAX_MPL_FACES, replace=False)
        faces = faces[idx]
        face_colors = face_colors[idx]

    center = vertices.mean(axis=0)
    vertices = vertices - center
    scale = np.abs(vertices).max()
    if scale > 0:
        vertices = vertices / scale

    images = []
    for title, elev, azim in VIEWS[:max_pages]:
        fig = plt.figure(figsize=(8, 8))
        ax = fig.add_subplot(111, projection="3d")

        face_verts = vertices[faces]
        poly = Poly3DCollection(face_verts, alpha=0.85)
        if has_texture:
            poly.set_facecolor(face_colors[:, :3])
            poly.set_edgecolor("none")
        else:
            poly.set_facecolor("#5B9BD5")
            poly.set_edgecolor("#2F528F")
            poly.set_linewidth(0.1)
        ax.add_collection3d(poly)

        lim = 1.1
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_zlim(-lim, lim)
        ax.view_init(elev=elev, azim=azim)
        ax.set_title(f"{os.path.basename(path)} — {title}", fontsize=12, pad=10)
        ax.set_xlabel("X")
        ax.set_ylabel("Y")
        ax.set_zlabel("Z")

        info = f"{total_verts} vertices, {total_faces} faces"
        ax.text2D(0.02, 0.02, info, transform=ax.transAxes, fontsize=8, color="gray")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", pad_inches=0.2)
        plt.close(fig)
        buf.seek(0)
        images.append(Image.open(buf))

    return images


# public entry point


def render(path: str, **opts: Any) -> list:
    max_pages = opts.get("max_pages", DEFAULT_MAX_PAGES)
    budget = opts.get("budget", "large")

    try:
        images = render_blender(path, **opts)
        return _images_to_content(images, path, budget)
    except Exception as e:
        log.info("model3d: blender backend unavailable/failed (%s); falling back to pyrender", e)

    try:
        import trimesh  # noqa: F401
    except ImportError:
        raise RuntimeError('Missing dependency — install with: pip install "qwen-mm-plugins[viz]"')

    try:
        images = _render_pyrender_subprocess(path, max_pages)
        return _images_to_content(images, path, budget)
    except Exception as e:
        log.info("model3d: pyrender subprocess failed (%s); falling back to matplotlib", e)

    meshes, total_verts, total_faces = _load_scene(path)
    images = _render_matplotlib(meshes, path, total_verts, total_faces, max_pages)
    return _images_to_content(images, path, budget)


def _images_to_content(images: list, path: str, budget: str) -> list:
    """Convert view images to labeled MCP content blocks."""
    from qwen_mm_plugins_core.renderers import labeled_image

    basename = os.path.basename(path)
    content: list = []
    for i, img in enumerate(images):
        title = VIEWS[i][0] if i < len(VIEWS) else f"View {i + 1}"
        content.extend(labeled_image(f"[{basename} — {title}]", img, budget))
    return content


# blender (optional)


def render_blender(path: str, **opts: Any) -> list:
    """Render via Blender Cycles subprocess."""

    from PIL import Image

    from shared.syscmd import find_tool

    blender = find_tool("blender")

    max_pages = opts.get("max_pages", DEFAULT_MAX_PAGES)

    script = os.path.join(os.path.dirname(__file__), "_blender_render.py")
    tmp_dir = tempfile.mkdtemp(prefix="blender_render_")

    try:
        result = subprocess.run(
            [blender, "--background", "--python", script, "--", path, tmp_dir, str(max_pages)],
            capture_output=True,
            text=True,
            timeout=120,
        )

        json_line = None
        for line in reversed(result.stdout.splitlines()):
            if line.strip().startswith("{"):
                json_line = line.strip()
                break

        if json_line is None:
            stderr_tail = (result.stderr or "")[-500:]
            raise RuntimeError(f"Blender produced no JSON output. stderr: {stderr_tail}")

        data = json.loads(json_line)
        if "error" in data:
            raise RuntimeError(data["error"])

        total_verts = data.get("total_verts", 0)
        total_faces = data.get("total_faces", 0)

        images = []
        for entry in data["outputs"]:
            img = Image.open(entry["path"])
            _add_overlay(img, path, entry["title"], total_verts, total_faces)
            images.append(img)

        return images
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
