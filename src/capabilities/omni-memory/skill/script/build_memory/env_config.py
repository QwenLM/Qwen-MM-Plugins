"""Which model endpoints the BUILD talks to — the standalone mirror of the server's config.py.

Two halves. Above the marker, a copy of src/shared/env.py's lookup: environment first, then
~/.qwen-mm-plugins/config, then the default — for a GUI-launched harness that file is the only place a
credential lives, so a build reading os.environ alone would report "no API key" on a correctly
configured machine. Below the marker, a copy of the server's settings getters, so both sides resolve
the same endpoint, model and library root from the same keys.

A copy rather than an import because some harnesses install a skill by copying skill/ alone, leaving
neither `shared` nor the server package reachable; video-memory's skill carries an env_config.py for
the same reason. The shared region is pinned by
tests/test_omni_memory.py::test_the_two_config_readers_stay_identical.

Also runnable: `python env_config.py` prints KEY=VALUE for every config-file key not already in the
environment, matching video-memory's build_memory.sh contract, so a shell launcher can export them.
"""

import os


def _config_file():
    """Config file path (mirrors shared.env): QWEN_MM_CONFIG, else QWEN_MM_CONFIG_DIR/config,
    else ~/.qwen-mm-plugins/config."""
    override = os.environ.get("QWEN_MM_CONFIG")
    if override:
        return os.path.expanduser(override)
    base = os.environ.get("QWEN_MM_CONFIG_DIR") or "~/.qwen-mm-plugins"
    return os.path.join(os.path.expanduser(base), "config")


def _parse_config(text):
    """KEY=VALUE per line; skip blank/# lines; strip `export ` and surrounding quotes."""
    out = {}
    for line in text.splitlines():
        line = line.strip().removeprefix("export ").lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
            val = val[1:-1]
        key = key.strip()
        if key:
            out[key] = val
    return out


_CONFIG = None


def _config():
    global _CONFIG
    if _CONFIG is None:
        try:
            with open(_config_file(), encoding="utf-8") as f:
                _CONFIG = _parse_config(f.read())
        except (OSError, UnicodeDecodeError):
            _CONFIG = {}
    return _CONFIG


def get_env(name, default=None):
    """Read at CALL time. Precedence: environment > user config file > default."""
    val = os.environ.get(name)
    return val if val is not None else _config().get(name, default)


# ══════════════════ IDENTICAL IN env_config.py BELOW THIS LINE ══════════════════

# Mirrors shared.env.DEFAULT_DASHSCOPE_BASE_URL, which the build's copy of this file cannot import.
DEFAULT_DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_OMNI_MODEL = "qwen3.5-omni-plus"
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
