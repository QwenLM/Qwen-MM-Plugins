"""Grounding verifier (model-free).

VLM-based verification of the base model's grounded objects. This deployment has
no external perception model: the only scene evidence is the instance-level
``Reconstruction`` (``recon.get_instances(frame)`` grounded boxes) plus the frame
images (``recon._input_images``). This tool confirms a target's *identity* by
cropping its grounded bounding box region from the frame and asking the injected
VLM whether that region really shows the expected label — reducing false positives
from grounding.

Public method names and returned dict field names are preserved so callers are
unchanged. The VLM handle is injected via ``set_vlm_module`` (mirrors Reconstruct),
so methods do not take a ``vlm`` argument.
"""

from typing import Any, Dict, List, Optional, Union

import numpy as np

from qwen_mm_plugins_video_spatio.experts.base import CPUTool


class GroundingVerifier(CPUTool):
    """Verify grounded objects using VLM semantic understanding.

    Uses the kernel-injected ``vlm`` module to confirm whether a grounded
    bounding box actually shows the intended target, reducing false positives
    from base-model grounding.
    """

    TOOL_PROMPT_DESCRIPTION = """\
### tools.Verify — Grounding Verifier (CPU)

Confirm the base model's grounded objects using VLM semantic understanding.
Crops a grounded bounding box region from the frame and asks the `vlm` whether it
really shows the expected label, filtering out false positives from grounding.

**Requires**: the `vlm` module (auto-injected in kernel).

| Method | Signature | Returns | Description |
|--------|-----------|---------|-------------|
| `verify_object` | `(image, bbox, label)` | `dict` | Ask `vlm` whether the region in `bbox` (0-1000 or pixel) shows the target label |
| `verify_mask` | `(recon, frame, target, bbox=None)` | `dict` | Verify a grounded instance: crop its region from the frame and confirm identity |
| `filter_verified` | `(recon, frame)` | `list[int]` | Return indices of `recon.get_instances(frame)` that pass VLM verification |

**Example — verify a grounded object before trusting it**
```python
recon = tools.Reconstruct.Reconstruct(InputImages[:6], targets=["shelf"])
result = tools.Verify.verify_mask(recon, frame=0, target="shelf")
if result["verified"]:
    print(f"Confirmed: {result['label']} is present")
else:
    print(f"Likely false positive; vlm said: {result['vlm_answer']}")
```

**Example — filter out false positives across a frame's instances**
```python
recon = tools.Reconstruct.Reconstruct(InputImages[:6], targets=["chair"])
valid = tools.Verify.filter_verified(recon, frame=0)
insts = recon.get_instances(0)
print(f"Verified {len(valid)}/{len(insts)} grounded objects")
```
"""

    def __init__(self):
        self._vlm_module = None
        self._tracer = None

    def set_vlm_module(self, vlm_module, feedback_module=None):
        self._vlm_module = vlm_module

    # ------------------------------------------------------------------
    # Image / instance helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_pil(image) -> Any:
        from PIL import Image

        if hasattr(image, "to_pil"):
            return image.to_pil()
        if hasattr(image, "image"):
            return image.image
        if hasattr(image, "convert"):
            return image
        return Image.fromarray(np.asarray(image))

    @staticmethod
    def _crop_bbox(image, bbox) -> Any:
        """Crop an image to a bounding box ``[x1, y1, x2, y2]`` with padding.

        Accepts boxes in 0..1000 normalized coords or raw pixels; values <= 1000
        with a small image are treated as pixels, otherwise 0..1000 is rescaled.
        """
        img = GroundingVerifier._to_pil(image)
        w, h = img.size
        x1, y1, x2, y2 = [float(v) for v in bbox]
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)

        mx = max(x1, y1, x2, y2)
        if mx <= 1.0:  # 0..1
            x1, x2, y1, y2 = x1 * w, x2 * w, y1 * h, y2 * h
        elif mx <= 1000.0 and not (w <= 1000 and h <= 1000):
            x1, x2 = x1 * w / 1000.0, x2 * w / 1000.0  # 0..1000 → pixels
            y1, y2 = y1 * h / 1000.0, y2 * h / 1000.0
        # else: already pixel coords on a small image.

        pad_x = (x2 - x1) * 0.1
        pad_y = (y2 - y1) * 0.1
        x1 = max(0, int(x1 - pad_x))
        y1 = max(0, int(y1 - pad_y))
        x2 = min(w, int(x2 + pad_x))
        y2 = min(h, int(y2 + pad_y))
        if x2 <= x1 or y2 <= y1:
            return img
        return img.crop((x1, y1, x2, y2))

    @staticmethod
    def _frame_image(recon, frame: int):
        """Best-effort PIL image for ``frame`` from ``recon._input_images``."""
        fis = list(getattr(recon, "frame_indices", []) or [])
        stored = getattr(recon, "_input_images", None)
        if not stored:
            return None
        if frame in fis and fis.index(frame) < len(stored):
            return stored[fis.index(frame)]
        return stored[0]

    @staticmethod
    def _find_instance(insts: List[dict], target: Union[int, str]) -> Optional[dict]:
        if isinstance(target, int):
            return insts[target] if 0 <= target < len(insts) else None
        t = str(target).strip().lower()
        for it in insts:
            lab = str(it.get("label", "")).lower()
            iid = str(it.get("id", "")).lower()
            if t == lab or t in lab or lab in t or t in iid:
                return it
        return None

    @staticmethod
    def _is_yes(answer: Optional[str]) -> bool:
        a = (answer or "").strip().lower()
        if not a:
            return False
        head = a.split("no")[0]
        return "yes" in a and ("yes" in head or not a.startswith("no"))

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_object(self, image, bbox, label: str) -> Dict[str, Any]:
        """Ask the VLM whether the ``bbox`` region contains the target object.

        Args:
            image: PIL Image or FrameImage (the frame the box lives on).
            bbox: ``[x1, y1, x2, y2]`` grounded bounding box (0-1000 or pixels).
            label: expected object name.

        Returns:
            dict with ``verified`` (bool), ``label``, ``vlm_answer``, ``bbox``.
        """
        crop = self._crop_bbox(image, bbox)
        base_label = label.rsplit("_", 1)[0] if "_" in label else label

        answer = None
        if self._vlm_module is not None:
            try:
                answer = self._vlm_module.ask(crop, f"Does this region show a {base_label}? yes/no")
            except Exception:  # noqa: BLE001
                answer = None

        return {
            "verified": self._is_yes(answer),
            "label": label,
            "vlm_answer": answer if answer is not None else "vlm unavailable",
            "bbox": list(bbox),
        }

    def verify_mask(
        self, recon, frame: int, target: Union[int, str], bbox: Optional[List[float]] = None
    ) -> Dict[str, Any]:
        """Verify a grounded instance by cropping its bounding box region.

        Resolves ``target`` against ``recon.get_instances(frame)`` to get the
        grounded box (or uses the explicit ``bbox`` arg), crops that region from
        the frame image, and confirms the identity with the VLM.
        """
        insts = recon.get_instances(frame) if hasattr(recon, "get_instances") else []
        it = self._find_instance(insts, target)

        if bbox is None:
            bbox = it.get("bbox_1000") if it else None
        label = (it.get("label") if it else None) or (target if isinstance(target, str) else f"object_{target}")

        if bbox is None:
            return {
                "verified": False,
                "label": label,
                "vlm_answer": "No grounded box for target — nothing to verify",
                "bbox": None,
            }

        img = self._frame_image(recon, frame)
        if img is None:
            return {
                "verified": False,
                "label": label,
                "vlm_answer": "Frame image unavailable",
                "bbox": list(bbox),
            }
        return self.verify_object(img, bbox, label)

    def filter_verified(self, recon, frame: int) -> List[int]:
        """Return indices of grounded instances that pass VLM verification.

        Iterates ``recon.get_instances(frame)`` and keeps only those whose
        bounding box region the VLM confirms shows the expected label.
        """
        insts = recon.get_instances(frame) if hasattr(recon, "get_instances") else []
        verified_indices: List[int] = []
        for i in range(len(insts)):
            try:
                result = self.verify_mask(recon, frame, i)
                if result["verified"]:
                    verified_indices.append(i)
            except Exception:  # noqa: BLE001
                continue
        return verified_indices

    def __repr__(self) -> str:
        return "GroundingVerifier(methods: verify_object, verify_mask, filter_verified)"
