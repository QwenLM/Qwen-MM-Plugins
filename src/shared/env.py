"""Tunable constants + environment access for the shared library and every capability.

Import `get_env` (call-time accessor for any env var) instead of calling os.getenv across the
codebase, so this stays the one place that reads the environment. Cross-cutting knobs (patch-grid
unit, resolution budgets, size/timeout limits via QWEN_MM_* vars) live here as constants;
capability-private env vars keep their own defaults in the capability that owns them.
"""

from __future__ import annotations

import os
from collections.abc import Iterable


def get_env(name: str, default: str | None = None) -> str | None:
    """Env config, read at CALL time. Precedence: environment > user config file > default.

    The config-file fallback lets GUI-launched harnesses (Codex/Claude desktop) find
    DASHSCOPE_API_KEY etc. — they don't inherit a shell's exported vars. See config_file.
    """
    val = os.environ.get(name)
    return val if val is not None else _config().get(name, default)


# ── User config file (~/.qwen-mm-plugins/config): KEY=VALUE lines, read when a var isn't in the
# environment. Location is fixed (not per-OS like cache_dir): "where is the config" can't live in
# the config, and pointing at it via env var would reintroduce the inheritance problem it solves. ──


def config_dir() -> str:
    """Fixed config dir (~/.qwen-mm-plugins), overridable via QWEN_MM_CONFIG_DIR."""
    return os.path.expanduser(os.environ.get("QWEN_MM_CONFIG_DIR") or "~/.qwen-mm-plugins")


def config_file() -> str:
    """Config file path, overridable via QWEN_MM_CONFIG (full path)."""
    override = os.environ.get("QWEN_MM_CONFIG")
    return os.path.expanduser(override) if override else os.path.join(config_dir(), "config")


def _parse_config(text: str) -> dict[str, str]:
    """Minimal dotenv parse: KEY=VALUE per line; skip blank/# lines; strip `export ` and quotes.
    Stdlib-only on purpose — the package floor is 3.10, so tomllib (3.11+) isn't guaranteed."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip().removeprefix("export ").lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        if key.strip():
            out[key.strip()] = val
    return out


_config_cache: dict[str, str] | None = None


def _config() -> dict[str, str]:
    """Parsed config, loaded once and cached (empty if missing/unreadable)."""
    global _config_cache
    if _config_cache is None:
        try:
            with open(config_file(), encoding="utf-8") as f:
                _config_cache = _parse_config(f.read())
        except (OSError, UnicodeDecodeError):
            _config_cache = {}
    return _config_cache


_CONFIG_HEADER = "# qwen-mm-plugins config — KEY=VALUE per line, read when the var isn't in the environment.\n\n"


def _write_config(path: str, merged: dict[str, str]) -> None:
    """Atomically write `merged` as a sorted dotenv file (0600) and invalidate the cache."""
    global _config_cache
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(_CONFIG_HEADER + "".join(f"{k}={merged[k]}\n" for k in sorted(merged)))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    _config_cache = None  # invalidate cache


def set_config(values: dict[str, str | None]) -> str:
    """Merge non-None `values` into the config file (atomic, 0600) and return its path.
    Used by `--setup`/`--set`; install.sh writes the same file directly."""
    path = config_file()
    try:
        with open(path, encoding="utf-8") as f:
            merged = _parse_config(f.read())
    except OSError:
        merged = {}
    merged.update({k: v for k, v in values.items() if v is not None})
    _write_config(path, merged)
    return path


def del_config(keys: Iterable[str]) -> str:
    """Remove `keys` from the config file (atomic, 0600) and return its path. No-op if none present.

    Clearing must delete the line, not write `KEY=` — an empty config value would shadow a real
    default (e.g. an empty QWEN_MM_CACHE would break the OS-default cache dir). Used by `--setup`
    (blank keeps, `-` clears) and `--unset`."""
    path = config_file()
    try:
        with open(path, encoding="utf-8") as f:
            merged = _parse_config(f.read())
    except OSError:
        return path
    present = [k for k in keys if k in merged]
    if not present:
        return path
    for k in present:
        del merged[k]
    _write_config(path, merged)
    return path


# ── Config-field catalog: the ONE declarative list of user-settable config vars, driving the
# interactive `--setup` (grouped) and documenting what belongs in the config file. Each entry is
# (key, secret, group, default, description) — `default` is the effective value or a concise
# default hint when the var is unset ("" = no default / required / off). Group order = order of
# first appearance.
# install.sh mirrors this list (CONFIG_SPEC) for its own editor — keep the two in sync when adding a
# var. Excludes the config-location bootstrap (QWEN_MM_CONFIG/_DIR — can't live in the config it
# locates), the example demo var, and behavioral on/off toggles (QWEN_MM_AUTOLAUNCH/…, FREECAD_* flags). ──
CONFIG_FIELDS: list[tuple[str, bool, str, str, str]] = [
    # Media APIs & endpoints
    (
        "DASHSCOPE_API_KEY",
        True,
        "Media APIs & endpoints",
        "",
        "vision, OCR, grounding, ASR, generation, memory builds",
    ),
    (
        "DASHSCOPE_BASE_URL",
        False,
        "Media APIs & endpoints",
        "DashScope compat URL",
        "override the DashScope OpenAI-compatible base URL",
    ),
    ("SAM3_SERVER_URL", False, "Media APIs & endpoints", "", "segmentation SAM3 server URL"),
    ("ASR_SERVER_URLS", False, "Media APIs & endpoints", "", "self-hosted ASR fallback URLs (comma-separated)"),
    # Search providers
    (
        "QWEN_MM_SEARCH_BACKEND",
        False,
        "Search providers",
        "auto",
        "text search backend (auto: serper > tavily > exa; or choose one)",
    ),
    (
        "SERPER_API_KEY",
        True,
        "Search providers",
        "",
        "Serper web_search / web_extractor and Serper-only image_search",
    ),
    ("TAVILY_API_KEY", True, "Search providers", "", "Tavily web_search / web_extractor"),
    ("EXA_API_KEY", True, "Search providers", "", "Exa web_search / web_extractor"),
    # Runtime paths & limits
    ("QWEN_MM_CACHE", False, "Runtime paths & limits", "OS cache dir", "cache dir for derived render artifacts"),
    ("QWEN_MM_FFMPEG_TIMEOUT", False, "Runtime paths & limits", "120", "ffmpeg/ffprobe timeout seconds"),
    (
        "QWEN_MM_CHAT_TIMEOUT",
        False,
        "Runtime paths & limits",
        "tool-specific (600; Omni 1800)",
        "OpenAI-compatible chat request timeout seconds",
    ),
    ("QWEN_MM_MAX_TOTAL_FRAMES", False, "Runtime paths & limits", "600", "max frames sampled from a video"),
    # OSS storage (serve large media by URL)
    ("OSS_AK", True, "OSS storage (serve large media by URL)", "", "OSS access key id"),
    ("OSS_SK", True, "OSS storage (serve large media by URL)", "", "OSS access key secret"),
    ("OSS_ENDPOINT", False, "OSS storage (serve large media by URL)", "", "OSS endpoint"),
    (
        "OSS_BUCKET",
        False,
        "OSS storage (serve large media by URL)",
        "",
        "upload destination for build clips and oversized API media",
    ),
    (
        "OSS_VIDEO_CLIP_PREFIX",
        False,
        "OSS storage (serve large media by URL)",
        "tmp/video_clips",
        "key prefix for uploaded video clips",
    ),
    ("OSS_URL_EXPIRY", False, "OSS storage (serve large media by URL)", "7200", "signed-URL TTL seconds"),
    # Video-memory
    ("GRAPH_MEMORY_PATH", False, "Video-memory", "", "graph_memory.json path (overrides a passed video path)"),
    ("EMBEDDINGS_PATH", False, "Video-memory", "", "embeddings.npz path"),
    ("CUTOFF_SEC", False, "Video-memory", "", "time cutoff (seconds) for retrieval"),
    # Blender / FreeCAD hosts
    ("BLENDER_BINARY", False, "Blender / FreeCAD hosts", "", "path to the Blender executable"),
    ("BLENDER_HOST", False, "Blender / FreeCAD hosts", "localhost", "Blender addon host"),
    ("BLENDER_PORT", False, "Blender / FreeCAD hosts", "9876", "Blender addon port"),
    ("FREECAD_BINARY", False, "Blender / FreeCAD hosts", "", "path to the FreeCAD executable"),
    ("FREECAD_RPC_HOST", False, "Blender / FreeCAD hosts", "localhost", "FreeCAD RPC host"),
    ("FREECAD_RPC_PORT", False, "Blender / FreeCAD hosts", "9875", "FreeCAD RPC port"),
    ("FREECAD_MOD_DIR", False, "Blender / FreeCAD hosts", "", "FreeCAD Mod dir for the bundled addon"),
    # edu-agent (Node / headless Chromium)
    ("NODE_PATH", False, "edu-agent (Node / headless Chromium)", "", "Node.js module resolution path"),
    (
        "PUPPETEER_EXECUTABLE_PATH",
        False,
        "edu-agent (Node / headless Chromium)",
        "",
        "headless Chromium executable for Puppeteer",
    ),
    # CUA proxy (the external driver itself owns OS permissions)
    (
        "QWEN_MM_CUA_DRIVER_PATH",
        False,
        "CUA proxy",
        "",
        "absolute path to cua-driver (overrides default locations and PATH)",
    ),
]


# Spatial patch size for one image token, just used to compute budgets.
TOKEN_SIZE = 32
# Default video FPS.
DEFAULT_FPS = 2.0
# Default budget for image/video processing.
DEFAULT_BUDGET = "normal"


def _int_env(var: str, default: int) -> int:
    """Parse an integer config var without crashing the server on a bad value.

    Accepts plain ints and human byte sizes (e.g. "15 MiB", "20MB", "1 GiB") — the latter so the
    values shown in the `--setup` hints work if a user copies them verbatim. Anything unparseable
    logs a warning and falls back to the default instead of raising at import time.
    """
    import re
    import sys

    raw = get_env(var)
    if not raw:
        return default
    raw = raw.strip()
    try:
        return int(raw)
    except ValueError:
        pass
    m = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMG]i?B)?", raw, re.IGNORECASE)
    if m:
        mult = {
            "": 1,
            "kb": 1000,
            "mb": 1000**2,
            "gb": 1000**3,
            "kib": 1024,
            "mib": 1024**2,
            "gib": 1024**3,
        }.get((m.group(2) or "").lower(), 1)
        return int(float(m.group(1)) * mult)
    sys.stderr.write(f"[qwen-mm-plugins] {var}={raw!r} is not a valid integer; using default {default}\n")
    return default


# Max bytes for a single tool response (image payloads are the big ones). Internal safety cap.
MAX_RESPONSE_BYTES = 15 * 1024 * 1024
# Timeout (seconds) for external ffmpeg/ffprobe calls.
FFMPEG_TIMEOUT = _int_env("QWEN_MM_FFMPEG_TIMEOUT", 120)
# Stream a tool result to stdout in chunks above this many bytes (keeps peak memory near one frame).
STREAM_THRESHOLD = 1024 * 1024
# Hard cap on frames returned by read_video.
MAX_TOTAL_FRAMES = _int_env("QWEN_MM_MAX_TOTAL_FRAMES", 600)

# OpenAI-compatible DashScope endpoint — the default when DASHSCOPE_BASE_URL is unset. Credentials
# (DASHSCOPE_API_KEY, OSS_*, …) are read at call time via get_env — there are no per-var accessors.
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# Per-preset resolution budgets (visual-token counts) → pixels via budget_to_pixels(budget, MAP).
IMAGE_BUDGET_TOKENS = {"small": 256, "normal": 1024, "large": 2048}
IMAGE_MIN_PIXELS = min(IMAGE_BUDGET_TOKENS.values()) * TOKEN_SIZE * TOKEN_SIZE

VIDEO_BUDGET_TOKENS = {"small": 80, "normal": 256, "large": 1024}
VIDEO_MIN_PIXELS = min(VIDEO_BUDGET_TOKENS.values()) * TOKEN_SIZE * TOKEN_SIZE
