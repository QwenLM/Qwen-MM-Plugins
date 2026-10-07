"""Standalone configuration reader and model settings for the Omni Memory builder.

The read-only region mirrors shared.env so a copied skill needs no installed server package.
The model settings below it mirror the server's config.py; tests check both shared regions.
Run this module to export config-file entries not already set in the environment.
"""

from __future__ import annotations

import logging
import os

# BEGIN STANDALONE ENV READER
# Synced to skill env_config.py files by scripts/sync_env_readers.py.

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def get_env(name: str, default: str | None = None, *, refresh_config: bool = False) -> str | None:
    """Env config, read at CALL time. Precedence: environment > user config file > default.

    The config-file fallback lets GUI-launched harnesses (Codex/Claude desktop) find
    DASHSCOPE_API_KEY etc. — they don't inherit a shell's exported vars. Set
    ``refresh_config`` for a long-lived process that must observe changes made by another
    process. See config_file.
    """
    global _config_cache
    val = os.environ.get(name)
    if val is not None:
        return val
    if refresh_config:
        _config_cache = None
    return _config().get(name, default)


def get_bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean config var at call time, returning ``default`` when unset or invalid."""
    raw = get_env(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    logging.getLogger(__name__).warning("invalid %s=%r; using default %d", name, raw, int(default))
    return default


def get_int_env(name: str, default: int) -> int:
    """Read a plain integer, falling back for unset, blank, or invalid values.

    Unlike ``_int_env``, this does not accept byte-size units.
    """
    raw = get_env(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logging.getLogger(__name__).warning("invalid %s=%r; using default %d", name, raw, default)
        return default


def get_float_env(name: str, default: float | None = None) -> float | None:
    """Read a plain float, falling back for unset, blank, or invalid values.

    The float sibling of ``get_int_env`` — for a knob whose default is "unset" (``None``) rather
    than a number, so a mistyped value warns and degrades instead of raising.
    """
    raw = get_env(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logging.getLogger(__name__).warning("invalid %s=%r; using default %r", name, raw, default)
        return default


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


# END STANDALONE ENV READER


# ══════════════════ IDENTICAL IN env_config.py BELOW THIS LINE ══════════════════

# Mirrors shared.env.DEFAULT_DASHSCOPE_BASE_URL, which the build's copy of this file cannot import.
DEFAULT_DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_OMNI_MODEL = "qwen3.8-omni-flash"
DEFAULT_EMBED_MODEL = "text-embedding-v4"


def _api_key():
    """The shared DashScope credential."""
    return get_env("DASHSCOPE_API_KEY") or "EMPTY"


def _dashscope_url():
    """DashScope's OpenAI-compatible endpoint, honouring DASHSCOPE_BASE_URL.

    The same catalogued setting the api and video-memory capabilities read, so an international
    station or a corporate gateway is configured in one place for all of them.
    """
    return get_env("DASHSCOPE_BASE_URL") or DEFAULT_DASHSCOPE_URL


def chat_config():
    """(base_url, model, api_key) for the omni model: extraction, planning, answering, replay.

    Same DashScope endpoint, credential and Omni model setting as the api capability.
    """
    return (_dashscope_url(), get_env("QWEN_MM_API_OMNI_MODEL") or DEFAULT_OMNI_MODEL, _api_key())


def embed_config():
    """(base_url, model, api_key) for embeddings, api_key None when none is configured.

    EMBED_BASE_URL points embeddings at their own endpoint, which may want its own credential rather
    than the DashScope key.

    On the DashScope branch a missing key is reported as None rather than _api_key()'s "EMPTY"
    placeholder: DashScope has no anonymous mode, so dense retrieval can step aside instead of
    spending two doomed requests per query to find out. The placeholder still stands in behind
    EMBED_BASE_URL, where a self-hosted endpoint may want no credential and the OpenAI client rejects
    both None and "".
    """
    model = get_env("EMBED_MODEL_NAME") or DEFAULT_EMBED_MODEL
    base = get_env("EMBED_BASE_URL")
    if base:
        return (base, model, get_env("EMBED_API_KEY") or _api_key())
    return (_dashscope_url(), model, get_env("DASHSCOPE_API_KEY"))


def local_dir():
    """Explicit shared-library root, or empty when memories should live beside the video."""
    configured = get_env("MEM_LOCAL_DIR")
    return os.path.expanduser(configured) if configured else ""


if __name__ == "__main__":
    # KEY=VALUE for config keys not already in the environment (the environment always wins), one per
    # line, for a shell launcher to export before it starts python.
    for _k, _v in _config().items():
        if os.environ.get(_k) is None:
            print(f"{_k}={_v}")
