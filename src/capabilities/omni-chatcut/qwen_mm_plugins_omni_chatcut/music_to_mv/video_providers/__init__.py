"""Video-generation provider adapters for the Music2MV executor."""

from .base import VideoProvider, canonical_video_provider_name, create_video_provider
from .seedance import SeedanceProvider
from .wan30 import Wan30Provider

__all__ = [
    "VideoProvider",
    "SeedanceProvider",
    "Wan30Provider",
    "canonical_video_provider_name",
    "create_video_provider",
]
