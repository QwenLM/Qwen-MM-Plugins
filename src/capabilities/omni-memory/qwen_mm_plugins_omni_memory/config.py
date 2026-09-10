"""Which model endpoints the SERVER talks to. Everything is settings-driven; see USAGE_NOTE in __init__.

Read through shared.env.get_env rather than os.environ, which adds the ~/.qwen-mm-plugins/config
fallback that `install.sh` and `--setup` write — the only way a GUI-launched harness (Claude desktop,
Codex) sees a credential, since those do not inherit a shell's exports. The environment still wins.

The build mirrors this file in skill/script/build_memory/env_config.py, which cannot import `shared`.
Everything below the marker is identical in both, pinned by
tests/test_omni_memory.py::test_the_two_config_readers_stay_identical: a build and a server that
disagree about the endpoint or model would fail in a way nobody thinks to look for.
"""

import os

from shared.env import get_env

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
