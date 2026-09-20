"""Motion expert: video motion segmentation, trajectory prediction, hand tracking.

Fills capability class 10 (运动预测与跟踪) gaps not covered by
``Mobile.track_object_trajectory`` (which recovers a past object track):
  - ``segment``: split a clip into moving/still segments by frame-difference
    motion magnitude (pure cv2/numpy, model-free).
  - ``predict_trajectory``: extrapolate a past 2D/3D track into future frames
    (pure numpy).
  - ``track_hands``: first-person left/right-hand 2D tracks via the base VLM.
"""

from typing import Any, Dict, List, Optional

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class MotionExpert(CPUTool):
    """Video motion segmentation / trajectory prediction / hand tracking.

    All methods are **static** — call directly on ``tools.Motion``.
    """

    TOOL_PROMPT_DESCRIPTION = """\
### tools.Motion — Motion Segmentation & Trajectory Expert (CPU + optional VLM)

Analyzes motion across a video clip. All methods are **static**.

| Method | Signature | Returns |
|--------|-----------|---------|
| `segment` | `(frames, motion_threshold=None)` | per-frame motion + moving/still clips (cv2, model-free) |
| `predict_trajectory` | `(past_points, n_future=3, order=2)` | extrapolated future points (numpy) |
| `track_hands` | `(frames, vlm)` | left/right hand 2D tracks (uses base VLM) |

**`segment` output**:
```python
{"num_frames": 60, "motion_per_frame": [0.0, 0.3, ...],
 "clips": [[0, 12], [30, 48]], "moving_ratio": 0.4}
```

**`predict_trajectory`**: `past_points` = list of [x,y] or [x,y,z]; fits a
low-order polynomial per axis and extrapolates `n_future` steps.

**`track_hands`**: returns `{"left_hand": [[t,x,y],...], "right_hand": [...]}`
in 0-1000 normalized coords (per available frame, via `vlm.locate`).

**Example**
```python
seg = tools.Motion.segment(InputImages)
print("active clips:", seg["clips"])
fut = tools.Motion.predict_trajectory([[0,0],[1,0.5],[2,1.2]], n_future=3)
```
"""

    # ------------------------------------------------------------------
    @staticmethod
    def _to_gray_arrays(frames) -> List[np.ndarray]:
        import cv2

        out = []
        for f in frames:
            img = f.image if hasattr(f, "image") else f
            arr = np.asarray(img.convert("RGB")) if hasattr(img, "convert") else np.asarray(img)
            if arr.ndim == 3:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            out.append(arr.astype(np.float32))
        return out

    @staticmethod
    def segment(frames, motion_threshold: Optional[float] = None, min_clip_len: int = 2) -> Dict[str, Any]:
        """Segment a clip into moving/still runs by mean abs frame difference."""
        grays = MotionExpert._to_gray_arrays(frames)
        n = len(grays)
        if n < 2:
            return {"num_frames": n, "motion_per_frame": [0.0] * n, "clips": [], "moving_ratio": 0.0}
        # normalize frames to a common size for diffing
        import cv2

        h0, w0 = grays[0].shape
        motion = [0.0]
        for i in range(1, n):
            g = grays[i]
            if g.shape != (h0, w0):
                g = cv2.resize(g, (w0, h0))
            prev = grays[i - 1]
            if prev.shape != (h0, w0):
                prev = cv2.resize(prev, (w0, h0))
            motion.append(float(np.mean(np.abs(g - prev)) / 255.0))
        motion_arr = np.array(motion)

        if motion_threshold is None:
            # adaptive: midpoint between still (min) and active (median of top half)
            motion_threshold = float(max(0.02, np.percentile(motion_arr, 60) * 0.5))

        moving = motion_arr > motion_threshold
        clips = []
        start = None
        for i, m in enumerate(moving):
            if m and start is None:
                start = i
            elif not m and start is not None:
                if i - start >= min_clip_len:
                    clips.append([start, i - 1])
                start = None
        if start is not None and n - start >= min_clip_len:
            clips.append([start, n - 1])

        return {
            "num_frames": n,
            "motion_per_frame": [round(x, 4) for x in motion],
            "motion_threshold": round(motion_threshold, 4),
            "clips": clips,
            "moving_ratio": round(float(moving.mean()), 3),
        }

    @staticmethod
    def predict_trajectory(past_points, n_future: int = 3, order: int = 2) -> Dict[str, Any]:
        """Extrapolate a past 2D/3D track into future steps via poly fit per axis."""
        pts = np.asarray(past_points, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[0] < 2:
            return {"future_points": [], "note": "need >=2 past points"}
        n_past, dim = pts.shape
        t = np.arange(n_past)
        deg = int(min(order, n_past - 1))
        t_future = np.arange(n_past, n_past + n_future)
        future = np.zeros((n_future, dim))
        for d in range(dim):
            coef = np.polyfit(t, pts[:, d], deg)
            future[:, d] = np.polyval(coef, t_future)
        return {
            "future_points": [[round(float(v), 3) for v in p] for p in future],
            "n_future": n_future,
            "fit_order": deg,
        }

    @staticmethod
    def track_hands(frames, vlm, max_frames: int = 12) -> Dict[str, Any]:
        """Per-frame left/right hand 2D tracks (0-1000 normalized) via base VLM."""
        import re

        plain = [f.image if hasattr(f, "image") else f for f in frames]
        n = len(plain)
        if n == 0:
            return {"left_hand": [], "right_hand": []}
        idx = np.linspace(0, n - 1, max_frames).round().astype(int).tolist() if n > max_frames else list(range(n))

        def _locate(img, which):
            q = (
                f"Locate the {which} hand in this first-person image. Reply as "
                f"(x=.., y=..) in 0-1000 normalized scale, or 'Not visible'."
            )
            try:
                ans = vlm.locate(img, q)
            except Exception:
                return None
            if not ans or "not visible" in ans.lower():
                return None
            nums = re.findall(r"[\d.]+", ans)
            if len(nums) >= 2:
                return [float(nums[0]), float(nums[1])]
            return None

        left, right = [], []
        for t in idx:
            img = plain[t]
            lh = _locate(img, "left")
            r = _locate(img, "right")
            if lh is not None:
                left.append([t, round(lh[0], 1), round(lh[1], 1)])
            if r is not None:
                right.append([t, round(r[0], 1), round(r[1], 1)])
        return {"left_hand": left, "right_hand": right, "frames_checked": idx}
