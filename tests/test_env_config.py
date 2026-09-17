"""Out-of-box robustness: a bad size/timeout config value must NOT crash the server.

`shared.env._int_env` parses the QWEN_MM_* size/timeout knobs at import time. A user who types a
human value like "15 MiB" (exactly what the `--setup` hint shows) — or plain garbage — must get a
sane value or fall back to the default, never a ValueError that aborts the whole MCP server before
the protocol handshake. These lock that in.
"""

from pathlib import Path

import pytest
from scripts.gen_env_docs import check, load_config_fields

from shared import env as env_config
from shared.env import _int_env, get_bool_env

_VAR = "QMP_TEST_INT_ENV"
_BOOL_VAR = "QMP_TEST_BOOL_ENV"
_ROOT = Path(__file__).resolve().parents[1]


def test_configuration_reference_matches_config_catalog():
    docs = [_ROOT / "docs/en/configuration.md"]
    assert check(load_config_fields(), docs) == 0


def test_plain_int(monkeypatch):
    monkeypatch.setenv(_VAR, "4242")
    assert _int_env(_VAR, 1) == 4242


def test_unset_uses_default(monkeypatch):
    monkeypatch.delenv(_VAR, raising=False)
    assert _int_env(_VAR, 777) == 777


@pytest.mark.parametrize(
    "val,expected",
    [
        ("15 MiB", 15 * 1024 * 1024),  # the exact string the --setup hint suggests
        ("1 MiB", 1024 * 1024),
        ("20MB", 20 * 1000 * 1000),
        ("2 GiB", 2 * 1024**3),
        ("512", 512),
        ("  64KiB ", 64 * 1024),
    ],
)
def test_human_sizes_parse(monkeypatch, val, expected):
    monkeypatch.setenv(_VAR, val)
    assert _int_env(_VAR, 0) == expected


def test_garbage_falls_back_without_raising(monkeypatch):
    # Used to crash the server at import with ValueError; now warns to stderr and uses the default.
    monkeypatch.setenv(_VAR, "not-a-number")
    assert _int_env(_VAR, 99) == 99


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "  On  "])
def test_bool_env_accepts_true_spellings(monkeypatch, value):
    monkeypatch.setenv(_BOOL_VAR, value)
    assert get_bool_env(_BOOL_VAR) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "  Off  "])
def test_bool_env_accepts_false_spellings(monkeypatch, value):
    monkeypatch.setenv(_BOOL_VAR, value)
    assert get_bool_env(_BOOL_VAR, default=True) is False


def test_bool_env_unset_or_invalid_uses_default(monkeypatch, caplog):
    monkeypatch.delenv(_BOOL_VAR, raising=False)
    assert get_bool_env(_BOOL_VAR, default=True) is True

    monkeypatch.setenv(_BOOL_VAR, "maybe")
    assert get_bool_env(_BOOL_VAR, default=True) is True
    assert f"invalid {_BOOL_VAR}" in caplog.text


def test_get_env_can_refresh_config_changed_by_another_process(tmp_path, monkeypatch):
    """A caller can opt in to seeing config written by a separate setup command."""
    config = tmp_path / "config"
    replacement = tmp_path / "config.new"
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.setattr(env_config, "_config_cache", None)

    config.write_text("DASHSCOPE_BASE_URL=https://first.example/v1\n", encoding="utf-8")
    assert env_config.get_env("DASHSCOPE_BASE_URL") == "https://first.example/v1"

    replacement.write_text("DASHSCOPE_BASE_URL=https://second.example/v1\n", encoding="utf-8")
    replacement.replace(config)
    assert env_config.get_env("DASHSCOPE_BASE_URL") == "https://first.example/v1"
    assert env_config.get_env("DASHSCOPE_BASE_URL", refresh_config=True) == "https://second.example/v1"


def test_environment_override_does_not_need_config_refresh(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text("DASHSCOPE_BASE_URL=https://config.example/v1\n", encoding="utf-8")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://environment.example/v1")
    monkeypatch.setattr(env_config, "_config_cache", None)

    assert env_config.get_env("DASHSCOPE_BASE_URL", refresh_config=True) == "https://environment.example/v1"
