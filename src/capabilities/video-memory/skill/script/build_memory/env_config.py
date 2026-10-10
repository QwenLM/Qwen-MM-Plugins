"""Standalone configuration reader for the video-memory builder.

The read-only region mirrors shared.env so a copied skill needs no installed server package.
Requires pydantic>=2.11,<3, installed by build_memory.sh. Running this module directly retains the
legacy KEY=VALUE export mode; Python callers read through get_env for automatic file refresh.
"""

from __future__ import annotations

import os

# BEGIN STANDALONE ENV READER
# Synced to skill env_config.py files by scripts/sync_env_readers.py.
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, FiniteFloat, TypeAdapter, ValidationError
from pydantic_core import PydanticCustomError


def _plain_integer(value):
    # Environment integers deliberately reject decimal/unit syntax, including "1.0".
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        raise PydanticCustomError("int_parsing", "Input should be a valid integer") from None


EnvInt = Annotated[int, BeforeValidator(_plain_integer)]
EnvBool = Annotated[
    Literal["1", "0", "true", "false", "yes", "no", "on", "off"],
    BeforeValidator(lambda value: str(value).lower()),
    AfterValidator(lambda value: value in ("1", "true", "yes", "on")),
]
SearchBackend = Annotated[Literal["auto", "serper", "tavily", "exa", "serply"], BeforeValidator(str.lower)]

# Ordinary settings: group -> name -> (Pydantic type, Field).
# This is the single definition of defaults, constraints and Configure/docs metadata.
# repr=False marks credentials; None means unset. Private fields use the same typed readers.
CONFIG_FIELDS = {
    "Media APIs & endpoints": {
        "DASHSCOPE_API_KEY": (
            str | None,
            Field(
                default=None,
                description="vision, OCR, grounding, text-only image captions, ASR, generation, memory builds",
                repr=False,
            ),
        ),
        "ORCAROUTER_API_KEY": (
            str | None,
            Field(default=None, description="OpenAI-compatible calls to api.orcarouter.ai", repr=False),
        ),
        "OPENROUTER_API_KEY": (
            str | None,
            Field(default=None, description="OpenAI-compatible calls to openrouter.ai", repr=False),
        ),
        "CHEAPER_INFERENCE_API_KEY": (
            str | None,
            Field(default=None, description="OpenAI-compatible calls to api.cheaperinference.com", repr=False),
        ),
        "MINIMAX_API_KEY": (
            str | None,
            Field(default=None, description="MiniMax text-to-speech generation", repr=False),
        ),
        "DASHSCOPE_BASE_URL": (
            str,
            Field(
                default="https://dashscope.aliyuncs.com/compatible-mode/v1",
                description="override the DashScope OpenAI-compatible base URL",
            ),
        ),
        "DASHSCOPE_UPLOAD_POLICY_URL": (
            str | None,
            Field(
                default=None,
                description="temporary OSS policy endpoint for oversized Omni and VL media; inferred for official DashScope hosts",
            ),
        ),
        "QWEN_MM_API_VL_MODEL": (
            str,
            Field(
                default="qwen3.7-plus",
                description="default VL model for vision_chat, OCR, grounding, text-only image captions, and video-spatio VLM tools",
            ),
        ),
        "QWEN_MM_API_OMNI_MODEL": (
            str,
            Field(
                default="qwen3.8-omni-flash",
                description="default Omni model for audio/video understanding tools, omni-memory, and Omni ChatCut",
            ),
        ),
        "SAM3_SERVER_URL": (str | None, Field(default=None, description="segmentation SAM3 server URL")),
        "ASR_SERVER_URLS": (
            str | None,
            Field(default=None, description="self-hosted ASR fallback URLs (comma-separated)"),
        ),
    },
    "Omni ChatCut": {
        "QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG": (
            str | None,
            Field(
                default=None, description="path to the shared Omni, image-provider, and video-provider connection JSON"
            ),
        ),
        "QWEN_MM_DUBBING_SERVER_URL": (
            str | None,
            Field(default=None, description="external IndexTTS2/Demucs/TEN-VAD service used by video translation"),
        ),
    },
    "Search providers": {
        "QWEN_MM_SEARCH_BACKEND": (
            SearchBackend,
            Field(
                default="auto", description="text search backend (auto: serper > tavily > exa > serply; or choose one)"
            ),
        ),
        "SERPER_API_KEY": (
            str | None,
            Field(
                default=None, description="Serper web_search / web_extractor and Serper-only image_search", repr=False
            ),
        ),
        "TAVILY_API_KEY": (
            str | None,
            Field(default=None, description="Tavily web_search / web_extractor", repr=False),
        ),
        "EXA_API_KEY": (str | None, Field(default=None, description="Exa web_search / web_extractor", repr=False)),
        "SERPLY_API_KEY": (
            str | None,
            Field(default=None, description="Serply web_search / web_extractor", repr=False),
        ),
    },
    "Runtime paths & limits": {
        "QWEN_MM_CACHE": (
            str | None,
            Field(default=None, description="cache dir for derived render artifacts; defaults to the OS cache dir"),
        ),
        "QWEN_MM_FFMPEG_TIMEOUT": (EnvInt, Field(default=120, description="ffmpeg/ffprobe timeout seconds", gt=0)),
        "QWEN_MM_CHAT_TIMEOUT": (
            EnvInt,
            Field(default=1800, description="OpenAI-compatible chat request timeout seconds", gt=0),
        ),
        "QWEN_MM_NATIVE_MODE": (
            EnvBool,
            Field(
                default=True, description="1 returns MCP images; 0 sends images to the VL endpoint and returns captions"
            ),
        ),
        "QWEN_MM_MAX_TOTAL_FRAMES": (EnvInt, Field(default=600, description="max frames sampled from a video", gt=0)),
    },
    "OSS storage (serve large media by URL)": {
        "OSS_AK": (str | None, Field(default=None, description="OSS access key id", repr=False)),
        "OSS_SK": (str | None, Field(default=None, description="OSS access key secret", repr=False)),
        "OSS_ENDPOINT": (str | None, Field(default=None, description="OSS endpoint")),
        "OSS_BUCKET": (
            str | None,
            Field(default=None, description="upload destination for build clips and oversized API media"),
        ),
        "OSS_VIDEO_CLIP_PREFIX": (
            str,
            Field(default="tmp/video_clips", description="key prefix for uploaded video clips"),
        ),
        "OSS_URL_EXPIRY": (EnvInt, Field(default=7200, description="signed-URL TTL seconds", gt=0)),
    },
    "Video-memory": {
        "GRAPH_MEMORY_PATH": (
            str | None,
            Field(default=None, description="graph_memory.json path (overrides a passed video path)"),
        ),
        "EMBEDDINGS_PATH": (str | None, Field(default=None, description="embeddings.npz path")),
        "CUTOFF_SEC": (
            FiniteFloat | None,
            Field(default=None, description="time cutoff (seconds) for retrieval", ge=0),
        ),
    },
    "Omni-memory": {
        "MEM_LOCAL_DIR": (
            str | None,
            Field(
                default=None, description="optional shared root for namespace memories; defaults beside the input video"
            ),
        ),
    },
    "Blender / FreeCAD hosts": {
        "BLENDER_BINARY": (str | None, Field(default=None, description="path to the Blender executable")),
        "BLENDER_HOST": (str, Field(default="localhost", description="Blender addon host")),
        "BLENDER_PORT": (EnvInt, Field(default=9876, description="Blender addon port", gt=0, le=65535)),
        "FREECAD_BINARY": (str | None, Field(default=None, description="path to the FreeCAD executable")),
        "FREECAD_RPC_HOST": (str, Field(default="localhost", description="FreeCAD RPC host")),
        "FREECAD_RPC_PORT": (EnvInt, Field(default=9875, description="FreeCAD RPC port", gt=0, le=65535)),
        "FREECAD_MOD_DIR": (str | None, Field(default=None, description="FreeCAD Mod dir for the bundled addon")),
    },
    "edu-agent (Node / headless Chromium)": {
        "NODE_PATH": (str | None, Field(default=None, description="Node.js module resolution path")),
        "PUPPETEER_EXECUTABLE_PATH": (
            str | None,
            Field(default=None, description="headless Chromium executable for Puppeteer"),
        ),
    },
}
_FIELD_SCHEMAS = {name: schema for fields in CONFIG_FIELDS.values() for name, schema in fields.items()}


def _default_text(value):
    return str(int(value)) if isinstance(value, bool) else str(value) if value is not None else ""


def config_catalog():
    """Configure/install/docs rows derived from the runtime schema; no configuration is read."""
    return [
        (name, not field.repr, group, _default_text(field.default), field.description)
        for group, fields in CONFIG_FIELDS.items()
        for name, (_, field) in fields.items()
    ]


_CONFIG_DEFAULTS = {
    name: _default_text(field.default) for name, (_, field) in _FIELD_SCHEMAS.items() if field.default is not None
}


class ConfigurationError(ValueError):
    """Invalid explicit configuration, suitable for returning as an MCP tool error."""


def _invalid_env(name: str, default, reason: str):
    remedy = "remove this optional override" if default is None else f"unset it to use {_default_text(default)!r}"
    # Never echo a configured value: it may contain a credential pasted into the wrong field.
    raise ConfigurationError(
        f"Invalid configuration {name}: {reason}. "
        f"Correct {name} in the process environment or config file, or {remedy}. "
        "Config-file changes apply on the next call; reconnect the MCP server after changing its environment."
    ) from None


def get_env(name: str, default: str | None = None, *, refresh_config: bool = False) -> str | None:
    """Read environment > config file > catalog default (or the private field's default).

    Whitespace is stripped; an empty winning value uses the default, without consulting a lower
    priority source. Catalogued defaults are authoritative across plugins. Reads detect config-file
    changes automatically; ``refresh_config`` also forces a read when file metadata is unchanged.
    """
    default = _CONFIG_DEFAULTS.get(name, default)
    val = os.environ.get(name)
    if val is None:
        val = _config(refresh=refresh_config).get(name)
    return (val or "").strip() or default


@lru_cache(maxsize=128)
def _env_adapter(name, annotation, default, min_value, max_value):
    annotation, field = _FIELD_SCHEMAS.get(name, (annotation, Field(default=default, ge=min_value, le=max_value)))
    return TypeAdapter(Annotated[annotation, field]), field.default


def _typed_env(name, annotation, default, min_value=None, max_value=None):
    adapter, default = _env_adapter(name, annotation, default, min_value, max_value)
    raw = get_env(name)
    try:
        return adapter.validate_python(default if raw is None else raw)
    except ValidationError as error:
        # Only Pydantic's safe reason, never its full repr/input/context (which may hold secrets).
        reason = error.errors(include_input=False, include_context=False, include_url=False)[0]["msg"]
        _invalid_env(name, default, reason)


def get_bool_env(name: str, default: bool = False) -> bool:
    """Read the documented boolean spellings, using the catalog's default and constraints."""
    return _typed_env(name, EnvBool, default)


def get_choice_env(name: str) -> str:
    """Read a case-insensitive choice declared in the catalog."""
    return _typed_env(name, str, None)


def get_int_env(name: str, default: int = 0, *, min_value: int | None = None, max_value: int | None = None) -> int:
    """Read a plain integer, using the catalog's default and constraints when declared."""
    return _typed_env(name, EnvInt, default, min_value, max_value)


def get_float_env(
    name: str,
    default: float | None = None,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float | None:
    """Read a finite number; private settings can supply a default and bounds here."""
    return _typed_env(name, FiniteFloat | None, default, min_value, max_value)


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
    import re

    out: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip().removeprefix("export ").lstrip()
        if not line or line.startswith("#"):
            continue
        key, separator, val = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigurationError(
                f"Invalid config file line {number}: expected KEY=VALUE with a valid variable name. "
                "Correct or remove that line and retry the call."
            ) from None
        val = val.strip()
        if val.startswith(("'", '"')) and (len(val) < 2 or val[-1] != val[0]):
            raise ConfigurationError(
                f"Invalid config file line {number}: expected a value with matching quotes. "
                "Correct or remove that line and retry the call."
            ) from None
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        out[key] = val
    return out


def _config_file_stamp(path: str) -> tuple:
    """Detect edits, atomic replacement, missing files, and changes of config location."""
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return (path, None)
    return (path, stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)


@lru_cache(maxsize=1)
def _read_config(path: str, stamp: tuple) -> dict[str, str]:
    """Cache by the pre-read metadata so edits during a read are detected on the next lookup."""
    try:
        with open(path, encoding="utf-8") as f:
            return _parse_config(f.read())
    except FileNotFoundError:
        return {}


def _config(*, refresh: bool = False) -> dict[str, str]:
    """Read the current file, reusing its parsed contents while metadata is unchanged."""
    if refresh:
        _read_config.cache_clear()
    path = os.path.abspath(config_file())
    try:
        return _read_config(path, _config_file_stamp(path))
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError(
            "Cannot read configuration: expected a readable UTF-8 config file. "
            "Check QWEN_MM_CONFIG / QWEN_MM_CONFIG_DIR, file permissions and encoding, then retry the call."
        ) from None


# END STANDALONE ENV READER
