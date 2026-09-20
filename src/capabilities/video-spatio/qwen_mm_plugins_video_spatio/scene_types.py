"""Per-frame typed data classes.

ALL array fields are ``numpy.ndarray`` (never ``torch.Tensor``).
Tools convert tensors to numpy internally before returning.

Every class carries ``frame_indices`` and enforces alignment via
``validate_alignment()``.
"""

from typing import Any, Dict, List, Optional

import numpy as np

from qwen_mm_plugins_video_spatio.visual_feedback import VisualFeedback


class PerFrameData:
    """Base class for all per-frame data."""

    def __init__(self, frame_indices: List[int]):
        self._frame_indices = list(frame_indices)

    @property
    def frame_indices(self) -> List[int]:
        return self._frame_indices

    @property
    def num_frames(self) -> int:
        return len(self._frame_indices)

    def validate_alignment(self, other: "PerFrameData", strict: bool = True) -> None:
        """Check that frame indices match between two PerFrameData objects.

        Raises ``ValueError`` with a clear message on mismatch.
        """
        if strict and self._frame_indices != other._frame_indices:
            raise ValueError(
                f"Frame index mismatch: {self._frame_indices} vs "
                f"{other._frame_indices}. Inputs must have identical "
                f"frame_indices for alignment."
            )

    def __iter__(self):
        raise TypeError(
            f"Cannot iterate over {self.__class__.__name__} directly. Use: for fi in obj.frame_indices: data = obj[fi]"
        )

    def __len__(self):
        return self.num_frames

    def get_by_frame_index(self, abs_frame_idx: int) -> int:
        """Return the local index for an absolute frame index."""
        if not isinstance(abs_frame_idx, (int, np.integer)):
            raise TypeError(
                f"Frame index must be an integer, got {type(abs_frame_idx).__name__}: {abs_frame_idx!r}.\n"
                f"  Single frame: obj[frame_idx]\n"
                f"  Batch array: obj.camera_poses (extrinsics), obj.points (point map), obj.depth (depth)"
            )
        if abs_frame_idx not in self._frame_indices:
            avail = self._frame_indices
            hint = ""
            if abs_frame_idx == 0 and 0 not in avail:
                hint = (
                    f"\n  Hint: Did you mean frame={avail[0]}? "
                    f"Use ABSOLUTE frame indices (from seg.frame_indices), not 0-based local indices."
                )
            raise KeyError(f"Frame {abs_frame_idx} not found in {avail}. Available frames: {avail}{hint}")
        return self._frame_indices.index(abs_frame_idx)

    def __repr__(self) -> str:
        fi = self._frame_indices
        if len(fi) > 6:
            fi_str = f"[{fi[0]}, {fi[1]}, ..., {fi[-2]}, {fi[-1]}]"
        else:
            fi_str = str(fi)
        return f"{self.__class__.__name__}(num_frames={self.num_frames}, frames={fi_str})"


class PerFrameIntrinsics(PerFrameData):
    """Per-frame camera intrinsics.

    Attributes:
        fx, fy, cx, cy: ``np.ndarray`` each of shape ``(N,)`` float64.
    """

    def __init__(
        self,
        fx: np.ndarray,
        fy: np.ndarray,
        cx: np.ndarray,
        cy: np.ndarray,
        frame_indices: List[int],
    ):
        super().__init__(frame_indices)
        self.fx = np.asarray(fx, dtype=np.float64)
        self.fy = np.asarray(fy, dtype=np.float64)
        self.cx = np.asarray(cx, dtype=np.float64)
        self.cy = np.asarray(cy, dtype=np.float64)

    def __getitem__(self, frame_idx: int) -> dict:
        """Get intrinsics dict by absolute frame index.

        Returns:
            ``dict(fx=float, fy=float, cx=float, cy=float)``
        """
        local = self.get_by_frame_index(frame_idx)
        return dict(
            fx=float(self.fx[local]),
            fy=float(self.fy[local]),
            cx=float(self.cx[local]),
            cy=float(self.cy[local]),
        )


class PerFrameExtrinsics(PerFrameData):
    """Per-frame camera extrinsics (camera-to-world SE(3)).

    Attributes:
        camera_poses: ``np.ndarray`` of shape ``(N, 4, 4)`` float64.
    """

    def __init__(
        self,
        camera_poses: np.ndarray,
        frame_indices: List[int],
    ):
        super().__init__(frame_indices)
        assert camera_poses.shape[0] == len(frame_indices)
        self.camera_poses = np.asarray(camera_poses, dtype=np.float64)

    def __getitem__(self, frame_idx: int) -> np.ndarray:
        """Get ``(4, 4)`` camera-to-world pose by absolute frame index."""
        local = self.get_by_frame_index(frame_idx)
        return self.camera_poses[local]


class Reconstruction(PerFrameData):
    """Model-free instance-level reconstruction output.

    Carries per-frame instances (``instances``) and camera poses (``cameras``)
    plus intrinsics/extrinsics. No dense point cloud or depth map is stored
    (``points``/``depth`` are always ``None``).

    BEV Rendering (``bev_visual``):
        Produces a top-down bird's-eye-view from ``instances`` + ``cameras``
        (object markers + camera trajectory). The reference camera's forward
        direction is aligned to the plot's upward direction (via ``ref_frame``).
    """

    def __init__(
        self,
        points,  # ★ removed: no dense point cloud (model-free) — always None
        depth,  # ★ removed: no dense depth (model-free) — always None
        intrinsics: PerFrameIntrinsics,
        extrinsics: PerFrameExtrinsics,
        metric_scale: float,
        gravity_direction: np.ndarray,
        rgb: Optional[np.ndarray] = None,
        frame_indices: Optional[List[int]] = None,
        segmenter=None,
        input_images=None,
        is_video: bool = True,
        instances: Optional[Dict[int, List[Dict[str, Any]]]] = None,  # ★ v2
        cameras: Optional[Dict[int, Dict[str, Any]]] = None,  # ★ v2
    ):
        fi = frame_indices if frame_indices is not None else (points.frame_indices if points is not None else [])
        super().__init__(fi)
        # ★ v2: points/depth are OPTIONAL — None when running model-free (prompt mode)
        self.points = points
        self.depth = depth
        self.intrinsics = intrinsics
        self.extrinsics = extrinsics
        self.metric_scale = metric_scale
        # Stores the "up" direction [0,1,0].  +Y = up in the aligned frame.
        # First camera forward is aligned to -Z.
        self.gravity_direction = np.asarray(gravity_direction, dtype=np.float64)
        self._rgb = rgb  # (N, H, W, 3) uint8 — from input frames
        self._segmenter = segmenter  # unused in model-free mode (kept for signature compat)
        self._is_video = is_video
        self._input_images = input_images  # PIL images for single-view / prompts
        # ★ v2: instance-level + camera-pose payload (see spec §1.1 / §1.2)
        self.instances: Dict[int, List[Dict[str, Any]]] = instances or {}
        self.cameras: Dict[int, Dict[str, Any]] = cameras or {}

    # ------------------------------------------------------------------
    # ★ v2 helpers (spec §1.3)
    # ------------------------------------------------------------------

    def get_instances(self, frame: int) -> List[Dict[str, Any]]:
        return list(self.instances.get(int(frame), []))

    def get_camera(self, frame: int) -> Optional[Dict[str, Any]]:
        return self.cameras.get(int(frame))

    def get_instance(self, canonical_id: str) -> Dict[int, Dict[str, Any]]:
        """Return {frame_idx -> Instance dict} for one canonical id (cross-frame track)."""
        out = {}
        for t, insts in self.instances.items():
            for it in insts:
                if it.get("id") == canonical_id:
                    out[t] = it
                    break
        return out

    def summary(self, frame: int) -> str:
        cam = self.get_camera(frame)
        L = [f"Frame t={frame}:"]
        if cam:
            L.append(
                f"  camera: pos_bev=({cam.get('pos_bev', (0, 0))[0]:.2f}, {cam.get('pos_bev', (0, 0))[1]:.2f}) m, "
                f"move_vec=({cam.get('move_vec_bev', (0, 0))[0]:+.2f}, {cam.get('move_vec_bev', (0, 0))[1]:+.2f}), "
                f"yaw={cam.get('yaw_deg', 0):+.1f}° (right+), pitch={cam.get('pitch_deg', 0):+.1f}° (up+)"
            )
        for it in self.get_instances(frame):
            L.append(
                f"  - {it.get('id', '?'):16s} label={it.get('label', '?'):12s} "
                f"bbox_1000={it.get('bbox_1000')}  depth={it.get('depth_m', 0):.2f}m  conf={it.get('conf', 0):.2f}"
            )
        return "\n".join(L)

    def summary_all(self) -> str:
        keys = sorted(set(list(self.instances.keys()) + list(self.cameras.keys())))
        return "\n\n".join(self.summary(t) for t in keys)

    # ------------------------------------------------------------------
    # BEV rendering
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal: motion-aware BEV object annotations
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # ★ v2: instance-level BEV (spec §四 / §1.1b)
    # Coordinate contract (spec §1.1b):
    #   BEV plane = ground XZ, meters, origin = reference camera.
    #   Horizontal = right(+), vertical = camera-forward(+).
    #   yaw: +right / -left ; pitch: +up / -down.
    # ------------------------------------------------------------------
    def bev_visual(
        self,
        ref_frame: int = 0,
        show_trajectory: bool = True,
        show_instances: bool = True,
        ids: Optional[List[str]] = None,
        x_range: Optional[tuple] = None,
        z_range: Optional[tuple] = None,
    ) -> "VisualFeedback":
        """Instance-level top-down BEV (model-free).

        Uses ``self.instances`` + ``self.cameras`` only (no point cloud).
        Coordinates follow spec §1.1b (meters, camera-origin, z=forward↑).
        """
        import io as _io
        import math as _math

        import matplotlib
        import numpy as _np

        matplotlib.use("Agg", force=False)
        import matplotlib.pyplot as _plt
        from matplotlib.lines import Line2D as _L2D
        from matplotlib.patches import FancyBboxPatch as _FBP
        from matplotlib.patches import Polygon as _Poly
        from PIL import Image as _Image

        cams = self.cameras or {}
        insts = self.instances or {}
        if not cams and not insts:
            raise RuntimeError(
                "bev_visual: recon has no instances/cameras "
                "(model-free perception must populate recon.instances / recon.cameras first)"
            )

        # ref frame camera → treated as origin (0,0), yaw=0 in the plot
        ref = cams.get(int(ref_frame)) or (list(cams.values())[0] if cams else None)
        ref_pos = _np.asarray(ref.get("pos_bev", (0.0, 0.0)), dtype=float) if ref else _np.zeros(2)
        ref_yaw = float(ref.get("yaw_deg", 0.0)) if ref else 0.0

        def _R(deg):
            r = _math.radians(deg)
            c, s = _math.cos(r), _math.sin(r)
            return _np.array([[c, -s], [s, c]])  # rotate (x, z)

        # world → plot: subtract ref origin, then rotate by -ref_yaw so ref forward=+z↑
        Rref = _R(-ref_yaw)

        def _to_plot(pos):
            v = _np.asarray(pos, dtype=float) - ref_pos
            return Rref @ v

        # figure
        fig, ax = _plt.subplots(figsize=(8, 8), facecolor="white")
        ax.set_facecolor("#eeeeee")
        ax.set_aspect("equal")
        ax.grid(True, color="white", lw=1.4, zorder=0)
        ax.set_axisbelow(True)
        ax.set_xlabel("← Left · · · Right →", fontsize=10, labelpad=4)
        ax.set_ylabel(f"Camera {ref_frame} Forward →", fontsize=10, labelpad=4)
        ax.set_title("Bird's Eye View (top-down)", fontsize=12, pad=10)

        def _pill(x, y, text, fc="white", ec="#666", fs=10, color="black", z=6):
            ax.text(
                x,
                y,
                text,
                ha="center",
                va="center",
                fontsize=fs,
                color=color,
                bbox=dict(boxstyle="round,pad=0.35", fc=fc, ec=ec, lw=0.8),
                zorder=z,
            )

        # gather points to size the plot
        pts = [(0.0, 0.0)]
        cam_plot = {}
        for t, cam in cams.items():
            cam_plot[t] = _to_plot(cam.get("pos_bev", (0.0, 0.0)))
            pts.append(tuple(cam_plot[t]))

        # canonical id -> color
        palette = [
            "#3a6fb0",
            "#e79126",
            "#2ca02c",
            "#9467bd",
            "#8c564b",
            "#e377c2",
            "#7f7f7f",
            "#bcbd22",
            "#17becf",
            "#d62728",
        ]
        color_by_id = {}

        def _color(cid):
            if cid not in color_by_id:
                color_by_id[cid] = palette[len(color_by_id) % len(palette)]
            return color_by_id[cid]

        # compute instance plot positions:
        # (x_m, z_m) = camera.pos_bev + R(camera.yaw_deg) · (0, depth_m)
        inst_positions = []  # (canon_id, label, pos_plot, seen_by_cam_plot, color, ambiguous)
        for t, cam in cams.items():
            yaw = float(cam.get("yaw_deg", 0.0))
            for it in insts.get(int(t), []) or []:
                if ids and it.get("id") not in ids:
                    continue
                depth = float(it.get("depth_m", 0.0) or 0.0)
                # forward in world frame at that camera (BEV plane)
                r = _math.radians(yaw)
                fwd_world = _np.array([_math.sin(r), _math.cos(r)])  # (x, z) world
                world_pos = _np.asarray(cam.get("pos_bev", (0, 0)), dtype=float) + fwd_world * depth
                p = _to_plot(world_pos)
                inst_positions.append(
                    (
                        it.get("id", "?"),
                        it.get("label", "?"),
                        p,
                        cam_plot.get(t, _np.zeros(2)),
                        _color(it.get("id", "?")),
                        bool(it.get("id_ambiguous", False)),
                        float(it.get("conf", 1.0) or 1.0),
                    )
                )
                pts.append(tuple(p))

        # axis range
        arr = _np.array(pts) if pts else _np.zeros((1, 2))
        pad = 1.2
        if x_range is None:
            x_range = (float(arr[:, 0].min() - pad), float(arr[:, 0].max() + pad))
        if z_range is None:
            z_range = (float(arr[:, 1].min() - pad), float(arr[:, 1].max() + pad))
        ax.set_xlim(*x_range)
        ax.set_ylim(*z_range)

        # four-direction pill labels
        cx = (x_range[0] + x_range[1]) / 2.0
        cy = (z_range[0] + z_range[1]) / 2.0
        _pill(cx, z_range[1] - 0.25, "FORWARD (away from camera)")
        _pill(cx, z_range[0] + 0.25, "BEHIND (toward camera)")
        _pill(x_range[0] + 0.30, cy, "LEFT")
        _pill(x_range[1] - 0.30, cy, "RIGHT")

        # trajectory
        if show_trajectory and len(cam_plot) >= 2:
            xs = [cam_plot[t][0] for t in sorted(cam_plot)]
            ys = [cam_plot[t][1] for t in sorted(cam_plot)]
            ax.plot(xs, ys, color="#888", lw=1.2, ls=(0, (4, 3)), zorder=3)

        # cameras
        def _draw_cam(pos_xy, yaw_deg, color, primary=False, tag=None):
            size = 0.28
            r = _math.radians(yaw_deg - ref_yaw)  # rotated to plot frame
            R = _np.array([[_math.cos(r), -_math.sin(r)], [_math.sin(r), _math.cos(r)]])
            local = _np.array([[-size * 0.75, -size * 0.6], [0, size], [size * 0.75, -size * 0.6]])
            world = (R @ local.T).T + _np.asarray(pos_xy)
            ax.add_patch(_Poly(world, closed=True, fc=color, ec=color, alpha=0.9, zorder=5))
            if primary:
                ax.plot([pos_xy[0]], [pos_xy[1]], "o", color=color, ms=5, zorder=6)
                _pill(pos_xy[0], pos_xy[1] - 0.45, f"Camera {ref_frame}", fc="white", ec=color, fs=10, color=color)
            elif tag is not None:
                _pill(pos_xy[0] + 0.03, pos_xy[1] - 0.28, str(tag), fc="white", ec="#2b6cff", fs=9, color="#2b6cff")

        for t, p in cam_plot.items():
            if int(t) == int(ref_frame):
                _draw_cam(p, cams[t].get("yaw_deg", 0.0), "#d33", primary=True)
            else:
                _draw_cam(p, cams[t].get("yaw_deg", 0.0), "#2b6cff", tag=t)

        # instances: line from seen-by camera + rounded box + label pill
        if show_instances:
            for cid, lbl, p, seen_p, col, amb, conf in inst_positions:
                ax.plot(
                    [seen_p[0], p[0]],
                    [seen_p[1], p[1]],
                    color=col,
                    lw=1.4,
                    alpha=0.9,
                    ls=(0, (3, 3)) if amb else "-",
                    zorder=4,
                )
                bw, bh = 0.55, 0.22
                ax.add_patch(
                    _FBP(
                        (p[0] - bw / 2, p[1] - bh / 2),
                        bw,
                        bh,
                        boxstyle="round,pad=0.02,rounding_size=0.08",
                        lw=0,
                        fc=col,
                        alpha=max(0.35, min(0.95, conf)),
                        zorder=5,
                    )
                )
                _pill(p[0], p[1], lbl, fc=col, ec=col, fs=9, color="white", z=7)

        # legend
        ax.legend(
            handles=[
                _L2D([0], [0], marker="^", color="w", mfc="#2b6cff", mec="#2b6cff", ms=10, label="Cameras"),
                _L2D([0], [0], marker="^", color="w", mfc="#d33", mec="#d33", ms=10, label=f"Camera {ref_frame}"),
            ],
            loc="upper right",
            fontsize=9,
            framealpha=0.9,
        )

        # bottom-left convention note
        conv = "yaw convention: +right / -left    pitch convention: +up / -down"
        ax.text(
            x_range[0] + 0.05,
            z_range[0] + 0.15,
            conv,
            fontsize=8,
            va="bottom",
            ha="left",
            color="#333",
            bbox=dict(fc="white", ec="#bbb", boxstyle="round,pad=0.35"),
        )

        buf = _io.BytesIO()
        _plt.tight_layout()
        _plt.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
        _plt.close(fig)
        buf.seek(0)
        img = _Image.open(buf).convert("RGB")
        return VisualFeedback(
            image=img,
            source="ReconstructPromptTool.bev_visual",
            description="Instance-level BEV (model-free, spec §四)",
        )

    def __repr__(self) -> str:
        fi = self._frame_indices
        if len(fi) > 6:
            fi_str = f"[{fi[0]}, {fi[1]}, ..., {fi[-2]}, {fi[-1]}]"
        else:
            fi_str = str(fi)
        return (
            f"Reconstruction(frames={fi_str}, num_frames={self.num_frames}, "
            f"metric_scale={self.metric_scale:.4f})\n"
            f"  Use: recon.depth[{fi[0]}]  # (H, W) depth\n"
            f"  Use: recon.extrinsics[{fi[0]}]  # (4, 4) c2w pose\n"
            f"  Use: seg.get_centroid_3d(recon, frame={fi[0]}, object=0)  # (3,) 3D position"
        )
