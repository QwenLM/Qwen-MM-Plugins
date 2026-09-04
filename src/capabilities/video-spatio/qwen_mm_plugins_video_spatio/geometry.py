"""Geometry utility module.  All operations on numpy arrays."""

from typing import Optional, Tuple

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


def _check_point(p, name: str, expected_dim: int = 3):
    """Validate a point/vector is array-like with the expected dimension."""
    arr = np.asarray(p)
    if arr.ndim != 1 or arr.shape[0] != expected_dim:
        raise ValueError(
            f"`{name}` must be a {expected_dim}D vector (shape ({expected_dim},)), "
            f"got shape {arr.shape}. "
            f"If you have a batch of points, index it first: points[i]"
        )
    return arr


class GeometryUtils(CPUTool):
    """Common geometric computation utilities.

    All methods operate on ``np.ndarray`` and return numpy types.
    """

    TOOL_PROMPT_DESCRIPTION = """
### tools.Geometry — Geometric Computation (CPU)

All methods are **static** — call directly on `tools.Geometry`.

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `euclidean_distance` | `(p1, p2)` | `float` | 3D Euclidean distance between two points |
| `angle_between_vectors` | `(v1, v2)` | `float` (degrees) | Angle between two 3D vectors |
| `project_point_to_camera` | `(point_3d, c2w, fx, fy, cx, cy)` | `(u, v)` or `None` | Project world point to pixel; `None` if behind camera |
| `rotation_matrix_from_vectors` | `(v_from, v_to)` | `(3, 3)` array | Rotation aligning v_from to v_to |
| `transform_points` | `(points, matrix)` | same shape | Apply (4, 4) SE(3) to (..., 3) points |
| `fit_ground_plane_ransac` | `(points, confidence)` | `(normal, mask)` or `(None, None)` | RANSAC ground plane |
| `normalized_to_pixel` | `(coords, width, height)` | `list[float]` | Convert 0-1000 normalized coords to pixels |

**Input**: `p1`, `p2`, `v1`, `v2`, `point_3d` must be **1D arrays of length 3**.
If you have per-pixel data `(H, W, 3)`, index the pixel first: `points[y, x]`.

```python
# 3D distance between two object centroids (using absolute frame index):
fi = seg.frame_indices[0]
c1 = seg.get_centroid_3d(recon, frame=fi, object=0)  # (3,)
c2 = seg.get_centroid_3d(recon, frame=fi, object=1)  # (3,)
dist = tools.Geometry.euclidean_distance(c1, c2)

# Convert 0-1000 normalized coords to pixels:
px, py = tools.Geometry.normalized_to_pixel((vlm_x, vlm_y), W, H)
```
"""

    @staticmethod
    def euclidean_distance(p1, p2) -> float:
        """3D Euclidean distance between two points."""
        p1 = np.asarray(p1, dtype=np.float64).ravel()
        p2 = np.asarray(p2, dtype=np.float64).ravel()
        if p1.shape != p2.shape:
            raise ValueError(f"p1 shape {p1.shape} and p2 shape {p2.shape} must match.")
        return float(np.linalg.norm(p1 - p2))

    @staticmethod
    def angle_between_vectors(v1, v2) -> float:
        """Angle in degrees between two vectors."""
        v1 = _check_point(v1, "v1", 3).astype(np.float64)
        v2 = _check_point(v2, "v2", 3).astype(np.float64)
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-12 or n2 < 1e-12:
            raise ValueError(f"Cannot compute angle with a zero-length vector. ||v1||={n1:.2e}, ||v2||={n2:.2e}")
        cos_angle = np.dot(v1, v2) / (n1 * n2)
        return float(np.degrees(np.arccos(np.clip(cos_angle, -1.0, 1.0))))

    @staticmethod
    def project_point_to_camera(
        point_3d,
        extrinsic_c2w: np.ndarray,
        fx: float,
        fy: float,
        cx: float,
        cy: float,
    ) -> Optional[Tuple[float, float]]:
        """Project a 3D world point onto the camera image plane.

        Args:
            point_3d: ``(3,)`` world coordinates.
            extrinsic_c2w: ``(4, 4)`` camera-to-world SE(3).
            fx, fy, cx, cy: Intrinsic parameters.

        Returns:
            ``(u, v)`` pixel coordinates, or ``None`` if the point is behind the camera.
        """
        point_3d = _check_point(point_3d, "point_3d", 3).astype(np.float64)
        extrinsic_c2w = np.asarray(extrinsic_c2w, dtype=np.float64)
        if extrinsic_c2w.shape != (4, 4):
            raise ValueError(
                f"`extrinsic_c2w` must be (4, 4), got shape {extrinsic_c2w.shape}. "
                f"Use recon.extrinsics.camera_poses[i] for a single frame."
            )
        w2c = np.linalg.inv(extrinsic_c2w)
        p_homo = np.append(point_3d, 1.0)
        p_cam = w2c @ p_homo
        if p_cam[2] <= 0:
            return None  # behind camera
        u = fx * p_cam[0] / p_cam[2] + cx
        v = fy * p_cam[1] / p_cam[2] + cy
        return (float(u), float(v))

    @staticmethod
    def rotation_matrix_from_vectors(v_from, v_to) -> np.ndarray:
        """Compute rotation matrix that rotates ``v_from`` to ``v_to``.

        Both inputs are ``(3,)`` vectors (need not be unit).
        Returns ``(3, 3)`` rotation matrix.
        """
        v_from = np.asarray(v_from, dtype=np.float64)
        v_to = np.asarray(v_to, dtype=np.float64)
        a = v_from / (np.linalg.norm(v_from) + 1e-12)
        b = v_to / (np.linalg.norm(v_to) + 1e-12)
        v = np.cross(a, b)
        c = np.dot(a, b)
        if np.linalg.norm(v) < 1e-8:
            # Vectors are (anti)parallel
            if c > 0:
                return np.eye(3)
            # 180-degree rotation about any perpendicular axis
            perp = np.array([1, 0, 0]) if abs(a[0]) < 0.9 else np.array([0, 1, 0])
            perp = perp - np.dot(perp, a) * a
            perp = perp / np.linalg.norm(perp)
            return 2 * np.outer(perp, perp) - np.eye(3)

        vx = np.array(
            [
                [0, -v[2], v[1]],
                [v[2], 0, -v[0]],
                [-v[1], v[0], 0],
            ]
        )
        return np.eye(3) + vx + vx @ vx / (1.0 + c)

    @staticmethod
    def transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
        """Apply a ``(4, 4)`` transformation matrix to ``(..., 3)`` points.

        Returns transformed points with the same shape.
        """
        if not isinstance(points, np.ndarray):
            raise TypeError(f"`points` must be numpy array, got {type(points).__name__}.")
        if points.shape[-1] != 3:
            raise ValueError(f"`points` last dimension must be 3, got shape {points.shape}.")
        matrix = np.asarray(matrix, dtype=np.float64)
        if matrix.shape != (4, 4):
            raise ValueError(f"`matrix` must be (4, 4), got shape {matrix.shape}.")
        original_shape = points.shape
        flat = points.reshape(-1, 3)
        ones = np.ones((flat.shape[0], 1), dtype=np.float64)
        homo = np.hstack([flat, ones])  # (N, 4)
        transformed = (matrix @ homo.T).T[:, :3]
        return transformed.reshape(original_shape)

    @staticmethod
    def fit_ground_plane_ransac(
        points: np.ndarray,
        confidence: np.ndarray,
        conf_threshold: float = 0.3,
        n_iterations: int = 1000,
        inlier_threshold: float = 0.05,
    ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """RANSAC ground plane fitting.

        Args:
            points: ``(H, W, 3)`` or ``(N, 3)`` point cloud.
            confidence: matching shape without last dim.
            conf_threshold: minimum confidence to include point.
            n_iterations: RANSAC iterations.
            inlier_threshold: distance to plane for inlier.

        Returns:
            ``(plane_normal, inlier_mask)`` or ``(None, None)`` if fitting fails.
        """
        pts = points.reshape(-1, 3)
        conf = confidence.reshape(-1)
        mask = conf > conf_threshold
        valid_pts = pts[mask]

        if len(valid_pts) < 100:
            return None, None

        best_normal = None
        best_inliers = 0
        best_mask = None
        rng = np.random.default_rng(42)

        for _ in range(n_iterations):
            idx = rng.choice(len(valid_pts), size=3, replace=False)
            p0, p1, p2 = valid_pts[idx]
            v1 = p1 - p0
            v2 = p2 - p0
            normal = np.cross(v1, v2)
            norm = np.linalg.norm(normal)
            if norm < 1e-8:
                continue
            normal = normal / norm

            # Distance from all points to the plane
            dists = np.abs(np.dot(valid_pts - p0, normal))
            inlier_mask = dists < inlier_threshold
            n_inliers = inlier_mask.sum()

            if n_inliers > best_inliers:
                best_inliers = n_inliers
                best_normal = normal
                best_mask = inlier_mask

        if best_normal is None or best_inliers < 50:
            return None, None

        # Refit plane from all inliers using SVD (least-squares)
        inlier_pts = valid_pts[best_mask]
        centroid = inlier_pts.mean(axis=0)
        _, _, vh = np.linalg.svd(inlier_pts - centroid, full_matrices=False)
        best_normal = vh[-1]  # last right singular vector = plane normal

        # NOTE: The normal sign is ambiguous.  The caller is responsible
        # for disambiguating (e.g. using the camera's known "up" direction).
        return best_normal, best_mask

    @staticmethod
    def normalized_to_pixel(
        coords,
        width: int,
        height: int,
    ) -> list:
        """Convert 0-1000 normalized coordinates to pixel coordinates.

        Args:
            coords: Coordinates in 0-1000 scale (x, y pairs).
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            List of pixel coordinates.
        """
        if any(v > 1000 for v in coords):
            print(
                f"[WARNING] normalized_to_pixel: coordinates {coords} exceed 1000 — "
                f"these may already be pixel coordinates. Normalized coords should be in 0-1000 scale. "
                f"If these are already pixels, use them directly without normalized_to_pixel()."
            )
        dims = [width, height] * (len(coords) // 2)
        if len(dims) < len(coords):
            # Odd number of coords — extend with width
            dims.extend([width] * (len(coords) - len(dims)))
        return [v / 1000.0 * d for v, d in zip(coords, dims)]

    @staticmethod
    def scale_from_known_size(observed_m: float, known_m: float) -> dict:
        """Metric scale correction from a known real-world size.

        Args:
            observed_m: The size as currently measured/estimated (meters).
            known_m: The true, known size (meters).

        Returns:
            ``{"scale_factor": known_m / observed_m, "observed_m", "known_m"}``.
        """
        observed = float(observed_m)
        if abs(observed) < 1e-6:
            raise ValueError(f"`observed_m` must be non-zero (>1e-6) to compute a scale factor, got {observed_m}.")
        return {
            "scale_factor": float(known_m) / observed,
            "observed_m": observed,
            "known_m": float(known_m),
        }

    @staticmethod
    def depth_from_known_width(bbox_px_width: float, known_width_m: float, fx: float) -> float:
        """Estimate depth (meters) from an object's known real width and its pixel width.

        ``depth = known_width_m * fx / bbox_px_width`` (pinhole model).
        """
        return float(known_width_m) * float(fx) / max(float(bbox_px_width), 1e-6)

    def __repr__(self) -> str:
        return (
            "GeometryUtils(static methods: euclidean_distance, angle_between_vectors, "
            "project_point_to_camera, rotation_matrix_from_vectors, transform_points, "
            "fit_ground_plane_ransac, normalized_to_pixel)"
        )
