"""JSON-lines logging to ~/.qwen-mm-plugins/logs/proxy.log (spec §8.4)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from shared.env import config_dir


def log_json(entry: dict) -> None:
    entry = dict(entry)
    entry.pop("api_key", None)  # 绝不落 key
    entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
    log_dir = os.path.join(config_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "proxy.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
