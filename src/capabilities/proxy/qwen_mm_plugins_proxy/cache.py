"""Two-tier in-memory description cache (spec §5.5): Tier1 (image) + Tier2 (image+question)."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

from .ir import ImageBlock

CACHE_CAPACITY = 500
CACHE_TTL_HOURS = 24


def image_key(image: ImageBlock) -> str:
    if image.url:
        return image.url
    raw = image.base64 or ""
    return "hash:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


class DescriptionCache:
    def __init__(self, capacity: int = CACHE_CAPACITY, ttl_hours: float = CACHE_TTL_HOURS):
        self.capacity = capacity
        self.ttl_seconds = ttl_hours * 3600
        self._store: OrderedDict[tuple[str, str | None], tuple[float, str]] = OrderedDict()

    def get(self, image_key_: str, question: str | None) -> str | None:
        key = (image_key_, question)
        item = self._store.get(key)
        if item is None:
            return None
        written_at, desc = item
        if time.time() - written_at > self.ttl_seconds:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return desc

    def put(self, image_key_: str, question: str | None, description: str) -> None:
        key = (image_key_, question)
        self._store[key] = (time.time(), description)
        self._store.move_to_end(key)
        while len(self._store) > self.capacity:
            self._store.popitem(last=False)
