from __future__ import annotations

import time

from qwen_mm_plugins_proxy.cache import DescriptionCache, image_key
from qwen_mm_plugins_proxy.ir import ImageBlock


def test_tier1_and_tier2_are_separate_keys():
    c = DescriptionCache()
    c.put("url1", None, "desc1")
    c.put("url1", "what is this?", "desc2")
    assert c.get("url1", None) == "desc1"
    assert c.get("url1", "what is this?") == "desc2"
    assert c.get("url1", "other question") is None


def test_lru_evicts_oldest():
    c = DescriptionCache(capacity=2)
    c.put("a", None, "A")
    time.sleep(0.01)
    c.put("b", None, "B")
    time.sleep(0.01)
    c.put("c", None, "C")  # evicts a
    assert c.get("a", None) is None
    assert c.get("b", None) == "B"
    assert c.get("c", None) == "C"


def test_image_key_uses_url_or_hash():
    assert image_key(ImageBlock(url="https://x/y.png")) == "https://x/y.png"
    assert image_key(ImageBlock(base64="QUJD"))  # 非空 sha256 前缀
