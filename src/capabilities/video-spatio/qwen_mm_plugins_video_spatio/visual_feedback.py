"""VisualFeedback: opaque wrapper for visual outputs from tools.

The agent can view the image by calling ``show(obj)`` to see it inline,
or pass it to ``vlm.locate(obj, ...)`` / ``vlm.ask_with_thinking(obj, ...)``
for VLM querying.
"""

from typing import Optional

from PIL import Image


class VisualFeedback:
    """Opaque image container returned by tools.

    Attributes:
        image: The underlying PIL.Image (never shown to the main agent).
        source: Human-readable origin, e.g. ``"Pi3.Reconstruct BEV"``.
        description: Auto-generated text description of the visual content.
        frame_index: Absolute video frame index, if applicable.
    """

    def __init__(
        self,
        image: Image.Image,
        source: str,
        description: str,
        frame_index: Optional[int] = None,
    ):
        if not isinstance(image, Image.Image):
            raise TypeError(f"VisualFeedback requires a PIL.Image, got {type(image).__name__}")
        self.image = image
        self.source = source
        self.description = description
        self.frame_index = frame_index

    def __repr__(self) -> str:
        parts = [
            f"source='{self.source}'",
            f"description='{self.description}'",
            f"size={self.image.size}",
        ]
        if self.frame_index is not None:
            parts.append(f"frame_index={self.frame_index}")
        return f"VisualFeedback({', '.join(parts)})"
