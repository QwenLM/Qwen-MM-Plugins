"""FrameImage: PIL Image wrapper that carries a frame_index.

Every ``InputImages[i]`` returns a ``FrameImage``, so downstream tools
(Reconstruct) can auto-extract frame indices without the caller passing
them explicitly.

``FrameImage`` delegates attribute access to the inner ``PIL.Image`` via
``__getattr__``, so ``.size``, ``.width``, ``.height``, ``.convert()``,
``__array_interface__`` (for ``np.array()``) all work transparently.

Supports lazy loading: when constructed with a file path instead of a PIL
Image, the image is loaded on first access.  This avoids loading hundreds
of frames upfront when only a few are actually used by the agent.
"""


class FrameImage:
    """PIL Image wrapper that carries a frame_index.  Supports lazy loading from path."""

    __slots__ = ("_image", "_path", "frame_index")

    def __init__(self, image_or_path, frame_index: int):
        if isinstance(image_or_path, FrameImage):
            self._image = image_or_path._image
            self._path = image_or_path._path
        elif isinstance(image_or_path, str):
            self._image = None
            self._path = image_or_path
        else:  # PIL Image
            self._image = image_or_path
            self._path = None
        self.frame_index = frame_index

    @property
    def image(self):
        if self._image is None and self._path is not None:
            from PIL import Image

            self._image = Image.open(self._path).convert("RGB")
        return self._image

    @property
    def is_loaded(self):
        return self._image is not None

    def __getattr__(self, name):
        # Guard against recursion during unpickling (slots not yet set)
        if name in ("_image", "_path", "frame_index"):
            raise AttributeError(name)
        return getattr(self.image, name)

    def __reduce__(self):
        # Prefer pickling path (lightweight) over loaded image
        if self._path is not None:
            return (FrameImage, (self._path, self.frame_index))
        return (FrameImage, (self._image, self.frame_index))

    def __repr__(self):
        if self._image is not None:
            return f"FrameImage(frame_index={self.frame_index}, size={self._image.size})"
        if self._path is not None:
            return f"FrameImage(frame_index={self.frame_index}, path='{self._path}')"
        return f"FrameImage(frame_index={self.frame_index}, uninitialized)"
