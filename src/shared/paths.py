"""Cross-platform filesystem path helpers."""

from __future__ import annotations

import os
from pathlib import Path


def path_to_file_uri(path: str | os.PathLike[str]) -> str:
    """Return an absolute, escaped ``file:`` URI for a local filesystem path."""

    return Path(path).expanduser().resolve().as_uri()
