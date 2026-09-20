"""MCP tool: Bird's-Eye View (BEV) visualization for spatial reconstruction."""

from __future__ import annotations

import base64
import io
import json
from typing import Any, Optional

from pydantic import BaseModel

from shared.content import image, require_dep, text, text_error


class VisualizeBevArgs(BaseModel):
    # Preferred: pass a `scene` from build_scene (auto-uses its instances/cameras + FOV)
    scene: Optional[Any] = None
    scene_file: Optional[str] = None
    viewpoint: Optional[Any] = None
    # Legacy: raw point-cloud path (compatibility)
    points_file: Optional[str] = None
    points_b64: Optional[str] = None
    masks_file: Optional[str] = None
    masks_b64: Optional[str] = None
    labels: Optional[list[str]] = None
    image_size: Optional[list[int]] = None
    xlim: Optional[list[float]] = None
    zlim: Optional[list[float]] = None
    resolution: float = 0.02
    title: str = "Bird's-Eye View"


TOOL: dict[str, Any] = {"name": "visualize_bev", "args": VisualizeBevArgs}


def _load_scene(arguments):
    s = arguments.get("scene")
    if s is None and arguments.get("scene_file"):
        s = json.load(open(arguments["scene_file"], "r", encoding="utf-8"))
    if isinstance(s, str):
        s = json.loads(s)
    return s if isinstance(s, dict) else None


def handle(arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Top-down BEV. Preferred: pass `scene` from build_scene + a `viewpoint` — the plot draws a DASHED
    forward/right cross-hair with Front/Back/Left/Right quadrant labels centred on that viewpoint
    (camera frame OR virtual viewpoint like 'stand at X facing Y'), so left/right/front/behind is a
    direct read of which quadrant an object sits in — no manual Pattern-B trig. Also shows camera pitch
    as an annotation (if |pitch|≥20° the BEV forward/back is flagged unreliable). Legacy: raw point
    cloud via points_file / points_b64 still works.

    Args:
        scene: `scene` object from build_scene (JSON or dict). Preferred input; instances/cameras drive
            the plot.
        scene_file: Path to a JSON file holding the scene (alternative to `scene`).
        viewpoint: Draw a DASHED forward/right cross-hair with Front/Back/Left/Right quadrant labels
            centred on a viewpoint. {frame:N}=camera frame N; virtual viewpoint =
            {at:[x,z]|label|{frame,label}, facing:label|[x,z]|heading_deg}. For 'facing the OPPOSITE
            direction of X' questions, use `facing_away:label|[x,z]|heading_deg` (the tool auto-adds
            180°); do NOT try to hand-derive the reverse angle. Turn any 'from X's perspective / left-
            right/front-behind' question into reading the object's quadrant.
        points_file: Legacy: .npy 3D point cloud (H,W,3). Prefer `scene` above.
        points_b64: Legacy: base64 numpy array of 3D points.
        masks_file: Legacy: .npy segmentation masks (N,H,W bool).
        masks_b64: Legacy: base64 numpy masks.
        labels: Labels for each mask object (legacy point-cloud path).
        image_size: Image dims [H,W] for scaling (legacy).
        xlim: X-axis limits [min,max] meters (default auto).
        zlim: Z-axis (depth) limits [min,max] meters (default auto).
        resolution: BEV grid resolution in meters per pixel (legacy path).
        title: Title for the BEV plot.
    """
    if err := require_dep("numpy"):
        return err
    import numpy as np

    # ── Preferred path: scene + viewpoint (dashed axes + Front/Back/Left/Right quadrants)
    scene = _load_scene(arguments)
    if scene is not None:
        try:
            img_b64, note = _render_bev_scene(
                scene,
                arguments.get("viewpoint"),
                arguments.get("title", "Bird's-Eye View"),
                arguments.get("xlim"),
                arguments.get("zlim"),
            )
            return [text(note), image(img_b64, "image/png")]
        except Exception as e:
            return text_error(f"rendering scene BEV: {e}")

    points = None
    if arguments.get("points_file"):
        points = np.load(arguments["points_file"])
    elif arguments.get("points_b64"):
        buf = io.BytesIO(base64.b64decode(arguments["points_b64"]))
        points = np.load(buf)

    if points is None:
        return text_error("provide `scene` (from build_scene) or legacy points_file/points_b64")

    masks = None
    if arguments.get("masks_file"):
        masks = np.load(arguments["masks_file"])
    elif arguments.get("masks_b64"):
        buf = io.BytesIO(base64.b64decode(arguments["masks_b64"]))
        masks = np.load(buf)

    labels = arguments.get("labels", []) or []
    resolution = arguments.get("resolution", 0.02)
    title = arguments.get("title", "Bird's-Eye View")

    try:
        bev_b64 = _render_bev(
            points,
            masks,
            labels,
            xlim=arguments.get("xlim"),
            zlim=arguments.get("zlim"),
            resolution=resolution,
            title=title,
        )
        return [text(f"BEV visualization generated ({title})"), image(bev_b64, "image/png")]
    except Exception as e:
        return text_error(f"rendering BEV: {e}")


def _render_bev(
    points,
    masks,
    labels,
    xlim=None,
    zlim=None,
    resolution=0.02,
    title="Bird's-Eye View",
) -> str:
    """Render a BEV image from 3D points with optional mask overlays."""
    import numpy as np

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
    except ImportError:
        return _render_bev_simple(points, masks, labels, xlim, zlim, resolution)

    pts_flat = points.reshape(-1, 3)
    valid = np.isfinite(pts_flat).all(axis=1) & (pts_flat[:, 2] > 0)
    pts_valid = pts_flat[valid]

    if pts_valid.shape[0] == 0:
        raise ValueError("No valid 3D points found")

    x, z = pts_valid[:, 0], pts_valid[:, 2]

    if xlim is None:
        xc = float(np.median(x))
        xr = max(float(np.percentile(x, 95) - np.percentile(x, 5)), 1.0)
        xlim = [xc - xr * 0.6, xc + xr * 0.6]
    if zlim is None:
        zmin = max(float(np.percentile(z, 2)), 0.1)
        zmax = float(np.percentile(z, 98))
        zlim = [zmin, zmax * 1.1]

    fig, ax = plt.subplots(1, 1, figsize=(8, 8), dpi=100)
    ax.set_facecolor("#1a1a2e")

    ax.scatter(x, z, s=0.3, c="#404060", alpha=0.3, rasterized=True)

    colors = [
        "#ff4444",
        "#44ff44",
        "#4488ff",
        "#ffff44",
        "#ff44ff",
        "#44ffff",
        "#ff8844",
        "#88ff44",
        "#4444ff",
        "#ff4488",
    ]
    legend_handles = []

    if masks is not None:
        h, w = points.shape[:2]
        for obj_idx in range(masks.shape[0]):
            mask = masks[obj_idx]
            if mask.shape != (h, w):
                continue
            obj_pts = points[mask > 0]
            obj_valid = np.isfinite(obj_pts).all(axis=1) & (obj_pts[:, 2] > 0)
            obj_pts = obj_pts[obj_valid]
            if obj_pts.shape[0] == 0:
                continue
            color = colors[obj_idx % len(colors)]
            ax.scatter(obj_pts[:, 0], obj_pts[:, 2], s=1.5, c=color, alpha=0.7, rasterized=True)
            label = labels[obj_idx] if obj_idx < len(labels) else f"Object {obj_idx}"
            legend_handles.append(mpatches.Patch(color=color, label=label))

    if legend_handles:
        ax.legend(handles=legend_handles, loc="upper right", fontsize=9)

    ax.set_xlim(xlim)
    ax.set_ylim(zlim)
    ax.set_xlabel("X (meters)", fontsize=10)
    ax.set_ylabel("Z / Depth (meters)", fontsize=10)
    ax.set_title(title, fontsize=12)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.2)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="#1a1a2e")
    plt.close(fig)

    return base64.b64encode(buf.getvalue()).decode("ascii")


def _render_bev_simple(points, masks, labels, xlim, zlim, resolution) -> str:
    """Fallback BEV renderer without matplotlib."""
    import numpy as np
    from PIL import Image

    pts_flat = points.reshape(-1, 3)
    valid = np.isfinite(pts_flat).all(axis=1) & (pts_flat[:, 2] > 0)
    pts_valid = pts_flat[valid]

    if pts_valid.shape[0] == 0:
        raise ValueError("No valid 3D points found")

    x, z = pts_valid[:, 0], pts_valid[:, 2]
    if xlim is None:
        xlim = [float(np.percentile(x, 5)), float(np.percentile(x, 95))]
    if zlim is None:
        zlim = [max(float(np.percentile(z, 2)), 0.1), float(np.percentile(z, 98))]

    w_px = int((xlim[1] - xlim[0]) / resolution)
    h_px = int((zlim[1] - zlim[0]) / resolution)
    w_px = max(100, min(w_px, 2000))
    h_px = max(100, min(h_px, 2000))

    img = Image.new("RGB", (w_px, h_px), (26, 26, 46))

    def to_px(xi, zi):
        px = int((xi - xlim[0]) / (xlim[1] - xlim[0]) * w_px)
        py = int((zi - zlim[0]) / (zlim[1] - zlim[0]) * h_px)
        return px, py

    for xi, zi in zip(x[::10], z[::10]):
        px, py = to_px(xi, zi)
        if 0 <= px < w_px and 0 <= py < h_px:
            img.putpixel((px, py), (64, 64, 96))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# Scene BEV with a dashed viewpoint cross-hair and Front/Back/Left/Right quadrants.
# ---------------------------------------------------------------------------


def _resolve_viewpoint(scene: dict, vp: Any):
    """Return (origin_x_m, origin_z_m, heading_deg, source) in world coords.

    vp forms accepted:
      - {"frame": N}                                        — camera N of the scene
      - {"at": [x,z] | label | {"frame":N,"label":..}, "facing": label|[x,z]|heading_deg}
      - None                                                 — camera 0 (default)
    heading_deg: +right / -left about +Y (same as scene's yaw_deg).
    """
    import math as _m

    cams = scene.get("cameras") or {}
    insts = scene.get("instances") or {}
    fis = scene.get("frame_indices") or [int(k) for k in cams]

    def cam_pose(fi):
        c = cams.get(str(fi)) or {}
        p = c.get("pos_bev") or [0.0, 0.0]
        return float(p[0]), float(p[1]), float(c.get("yaw_deg") or 0.0)

    def find_inst(target):
        """Return (x, z) world coords of an instance whose label ~= target (across frames)."""
        for _, insts_i in insts.items():
            for it in insts_i or []:
                if str(it.get("label", "")).lower() == str(target).lower():
                    ci = cams.get(str(it.get("frame_idx"))) or {}
                    pos = ci.get("pos_bev") or [0.0, 0.0]
                    yaw = _m.radians(float(ci.get("yaw_deg") or 0.0))
                    depth = float(it.get("depth_m") or 0.0)
                    pt = it.get("point_1000") or [500.0, 500.0]
                    fov = float(ci.get("fov_deg") or 60.0)
                    bearing = _m.radians((pt[0] / 1000.0 - 0.5) * fov)
                    # local (right, forward) then rotate by yaw into world
                    r = depth * _m.sin(bearing)
                    f = depth * _m.cos(bearing)
                    x = pos[0] + _m.sin(yaw) * f + _m.cos(yaw) * r
                    z = pos[1] - _m.cos(yaw) * f + _m.sin(yaw) * r
                    return (x, z)
        return None

    if not vp:
        x, z, yaw = cam_pose(fis[0] if fis else 0)
        return x, z, yaw, f"camera {fis[0] if fis else 0}"
    if isinstance(vp, str):
        try:
            vp = json.loads(vp)
        except Exception:
            vp = None
    if isinstance(vp, dict) and "frame" in vp and "at" not in vp:
        x, z, yaw = cam_pose(vp["frame"])
        return x, z, yaw, f"camera {vp['frame']}"
    at = (vp or {}).get("at")
    facing = (vp or {}).get("facing")
    facing_away = (vp or {}).get("facing_away")  # NEW: 面朝 X 的反方向
    if isinstance(at, (list, tuple)):
        ox, oz = float(at[0]), float(at[1])
    elif isinstance(at, str):
        got = find_inst(at)
        ox, oz = got if got else (0.0, 0.0)
    elif isinstance(at, dict) and "label" in at:
        got = find_inst(at["label"])
        ox, oz = got if got else (0.0, 0.0)
    else:
        ox, oz, _ = cam_pose(fis[0] if fis else 0)

    def _resolve_heading(spec):
        """Turn a facing spec into a heading angle in degrees."""
        if isinstance(spec, (int, float)):
            return float(spec)
        if isinstance(spec, (list, tuple)):
            dx, dz = float(spec[0]) - ox, float(spec[1]) - oz
            return _m.degrees(_m.atan2(dx, -dz))
        if isinstance(spec, str):
            got = find_inst(spec)
            if got:
                dx, dz = got[0] - ox, got[1] - oz
                return _m.degrees(_m.atan2(dx, -dz))
        return None

    if facing_away is not None:
        h = _resolve_heading(facing_away)
        heading = (h + 180.0) if h is not None else 0.0
        vp_desc_extra = f" facing AWAY from {facing_away}"
    else:
        h = _resolve_heading(facing)
        heading = h if h is not None else 0.0
        vp_desc_extra = ""
    # Normalize to [-180, 180]
    heading = ((heading + 540.0) % 360.0) - 180.0
    return ox, oz, heading, f"virtual at ({ox:.2f},{oz:.2f}) facing {heading:.0f}°{vp_desc_extra}"


def _render_bev_scene(scene: dict, vp: Any, title: str, xlim, zlim):
    import math as _m

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt

    cams = scene.get("cameras") or {}
    insts = scene.get("instances") or {}
    ox, oz, heading, vp_desc = _resolve_viewpoint(scene, vp)
    th = _m.radians(heading)  # rotate world into the viewpoint frame (heading → +Y_plot = forward/up)
    ct, st = _m.cos(th), _m.sin(th)

    def to_plot(px, pz):
        # Decompose the world offset (dx,dz) into the viewpoint's (right, forward) axes.
        # World convention: +X = right, -Z = forward, heading via atan2(dx,-dz) (+ = right).
        # Inverse of build_scene's placement → right = cosH·dx + sinH·dz ; forward = sinH·dx - cosH·dz.
        # (Validated by /tmp/bev_gt.py: a straight-ahead object lands FRONT for every heading.)
        dx, dz = px - ox, pz - oz
        xp = ct * dx + st * dz  # right axis  (+ = RIGHT of the viewpoint)
        yp = st * dx - ct * dz  # forward axis (+ = FRONT / up)
        return xp, yp

    fig, ax = _plt.subplots(figsize=(9, 9), facecolor="white")
    ax.set_facecolor("#f7f7fb")

    # collect points to size the plot
    pts_plot = []
    for fi, ins in insts.items():
        c = cams.get(str(fi)) or {}
        pos = c.get("pos_bev") or [0.0, 0.0]
        yaw = _m.radians(float(c.get("yaw_deg") or 0.0))
        fov = float(c.get("fov_deg") or 60.0)
        for it in ins or []:
            depth = float(it.get("depth_m") or 0.0)
            pt = it.get("point_1000") or [500.0, 500.0]
            bearing = _m.radians((pt[0] / 1000.0 - 0.5) * fov)
            r = depth * _m.sin(bearing)
            f = depth * _m.cos(bearing)
            wx = float(pos[0]) + _m.sin(yaw) * f + _m.cos(yaw) * r
            wz = float(pos[1]) - _m.cos(yaw) * f + _m.sin(yaw) * r
            xp, yp = to_plot(wx, wz)
            pts_plot.append((xp, yp, str(it.get("label", "?")), it.get("frame_idx")))
    for fi, c in cams.items():
        p = c.get("pos_bev") or [0.0, 0.0]
        xp, yp = to_plot(float(p[0]), float(p[1]))
        pts_plot.append((xp, yp, f"cam{fi}", None))

    if xlim is None or zlim is None:
        if pts_plot:
            xs = [p[0] for p in pts_plot]
            ys = [p[1] for p in pts_plot]
            r_ = max(3.0, max(abs(min(xs)), abs(max(xs)), abs(min(ys)), abs(max(ys))) * 1.25)
        else:
            r_ = 3.0
        xlim = xlim or [-r_, r_]
        zlim = zlim or [-r_, r_]

    # DASHED viewpoint cross-hair at origin (forward=up, right=right)
    ax.axhline(0, color="#333", linestyle="--", linewidth=1.4, alpha=0.85, zorder=2)
    ax.axvline(0, color="#333", linestyle="--", linewidth=1.4, alpha=0.85, zorder=2)
    # Front/Back/Left/Right labels at axis ends
    xr = xlim[1] * 0.92
    yt = zlim[1] * 0.92
    xl = xlim[0] * 0.92
    yb = zlim[0] * 0.92
    ax.text(
        0,
        yt,
        "FRONT",
        ha="center",
        va="top",
        fontsize=13,
        fontweight="bold",
        color="#2a7",
        zorder=5,
        bbox=dict(facecolor="white", edgecolor="#2a7", boxstyle="round,pad=0.25"),
    )
    ax.text(
        0,
        yb,
        "BACK",
        ha="center",
        va="bottom",
        fontsize=13,
        fontweight="bold",
        color="#c53",
        zorder=5,
        bbox=dict(facecolor="white", edgecolor="#c53", boxstyle="round,pad=0.25"),
    )
    ax.text(
        xr,
        0,
        "RIGHT",
        ha="right",
        va="center",
        fontsize=13,
        fontweight="bold",
        color="#37c",
        zorder=5,
        bbox=dict(facecolor="white", edgecolor="#37c", boxstyle="round,pad=0.25"),
    )
    ax.text(
        xl,
        0,
        "LEFT",
        ha="left",
        va="center",
        fontsize=13,
        fontweight="bold",
        color="#93c",
        zorder=5,
        bbox=dict(facecolor="white", edgecolor="#93c", boxstyle="round,pad=0.25"),
    )
    ax.plot(0, 0, marker="*", markersize=18, color="black", zorder=6)  # viewpoint origin

    # cameras (open triangle) + instances (filled dot with label)
    palette = ["#e04b4b", "#3d8be0", "#38a34a", "#c98a2a", "#8a44c4", "#0d9caf", "#c93b96"]
    label_color = {}
    for xp, yp, lbl, fi in pts_plot:
        if lbl.startswith("cam"):
            ax.plot(xp, yp, marker="^", markersize=10, markerfacecolor="none", markeredgecolor="black", zorder=4)
            ax.text(xp, yp - 0.15, lbl, ha="center", va="top", fontsize=8, color="#555", zorder=4)
        else:
            key = lbl.lower()
            c = label_color.setdefault(key, palette[len(label_color) % len(palette)])
            ax.plot(xp, yp, marker="o", markersize=9, color=c, zorder=4)
            ax.text(xp + 0.1, yp + 0.1, f"{lbl}" + (f"@{fi}" if fi is not None else ""), fontsize=9, color=c, zorder=4)

    max_abs_pitch = max((abs(float(c.get("pitch_deg") or 0.0)) for c in cams.values()), default=0.0)
    subtitle = f"viewpoint: {vp_desc}  |  camera max |pitch|={max_abs_pitch:.0f}°"
    if max_abs_pitch >= 20.0:
        subtitle += "  ⚠ BEV front/back may be unreliable (large pitch)"
    ax.set_title(f"{title}\n{subtitle}", fontsize=11)
    ax.set_xlim(xlim)
    ax.set_ylim(zlim)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.25)
    ax.set_xlabel("left ← 0 → right (m)  [plot x]", fontsize=9)
    ax.set_ylabel("back ← 0 → front (m)  [plot y]", fontsize=9)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    _plt.close(fig)
    note = (
        f"BEV rendered — {vp_desc}. Dashed axes = viewpoint frame; read each object's "
        f"quadrant (Front/Back × Left/Right) directly. max |camera pitch|={max_abs_pitch:.0f}°"
        + ("  ⚠ pitch≥20°: fall back to monocular Pattern A." if max_abs_pitch >= 20.0 else "")
    )
    return base64.b64encode(buf.getvalue()).decode("ascii"), note
