"""Configuration uses one contract: defaults for missing/blank, errors for invalid values."""

from pathlib import Path

import pytest
from scripts.gen_env_docs import check, load_config_fields

from shared import env as env_config
from shared.env import ConfigurationError, get_bool_env, get_int_env

_VAR = "QMP_TEST_INT_ENV"
_BOOL_VAR = "QMP_TEST_BOOL_ENV"
_PORT_VAR = "QMP_TEST_PORT_ENV"
_ROOT = Path(__file__).resolve().parents[1]


def test_configuration_reference_matches_config_catalog():
    docs = [_ROOT / "docs/en/configuration.md"]
    assert check(load_config_fields(), docs) == 0


def test_catalog_generation_does_not_read_runtime_configuration(tmp_path, monkeypatch):
    config = tmp_path / "invalid-config"
    config.write_text("malformed configuration\n")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setenv("QWEN_MM_CHAT_TIMEOUT", "private-invalid-value")
    fields = {row[0]: row[3] for row in load_config_fields()}
    assert fields["QWEN_MM_CHAT_TIMEOUT"] == "1800"


@pytest.mark.parametrize(
    "name,value", [("BLENDER_PORT", "0"), ("FREECAD_RPC_PORT", "65536"), ("QWEN_MM_CHAT_TIMEOUT", "0")]
)
def test_catalog_constraints_do_not_need_to_be_repeated_by_callers(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError, match=name):
        get_int_env(name)


def test_plain_int(monkeypatch):
    monkeypatch.setenv(_VAR, "4242")
    assert get_int_env(_VAR, 1) == 4242


def test_unset_uses_default(monkeypatch):
    monkeypatch.delenv(_VAR, raising=False)
    assert get_int_env(_VAR, 777) == 777


@pytest.mark.parametrize("value", ["15 MiB", "20MB", "2 GiB", "not-a-number", "1.5", "nan", "inf"])
def test_invalid_integer_raises_without_logging_value(monkeypatch, caplog, value):
    monkeypatch.setenv(_VAR, value)
    with pytest.raises(ConfigurationError, match=_VAR) as error:
        get_int_env(_VAR, 99)
    assert value not in str(error.value)
    assert value not in caplog.text


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "  On  "])
def test_bool_env_accepts_true_spellings(monkeypatch, value):
    monkeypatch.setenv(_BOOL_VAR, value)
    assert get_bool_env(_BOOL_VAR) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", "  Off  "])
def test_bool_env_accepts_false_spellings(monkeypatch, value):
    monkeypatch.setenv(_BOOL_VAR, value)
    assert get_bool_env(_BOOL_VAR, default=True) is False


def test_bool_env_only_unset_uses_default(monkeypatch, caplog):
    monkeypatch.delenv(_BOOL_VAR, raising=False)
    assert get_bool_env(_BOOL_VAR, default=True) is True

    monkeypatch.setenv(_BOOL_VAR, "maybe")
    with pytest.raises(ConfigurationError, match=_BOOL_VAR):
        get_bool_env(_BOOL_VAR, default=True)
    assert "maybe" not in caplog.text


@pytest.mark.parametrize(
    "value,expected",
    [(None, 9876), ("", 9876), ("   ", 9876), ("9876", 9876), ("  5000  ", 5000), ("0", 0)],
)
def test_int_env_valid_or_blank(monkeypatch, caplog, value, expected):
    if value is None:
        monkeypatch.delenv(_PORT_VAR, raising=False)
    else:
        monkeypatch.setenv(_PORT_VAR, value)
    assert get_int_env(_PORT_VAR, 9876) == expected


@pytest.mark.parametrize(
    "loader_mod,port_var",
    [("qwen_mm_plugins_blender.loader", "BLENDER_PORT"), ("qwen_mm_plugins_freecad.loader", "FREECAD_RPC_PORT")],
)
def test_blank_port_in_config_does_not_abort_startup(tmp_path, monkeypatch, loader_mod, port_var):
    """Blank config-file ports must not abort the MCP startup hook."""
    import importlib

    config = tmp_path / "config"
    config.write_text(f"{port_var}=\n", encoding="utf-8")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.delenv(port_var, raising=False)

    loader = importlib.import_module(loader_mod)
    loader.probe()
    assert env_config.get_env(port_var) == ("9876" if port_var == "BLENDER_PORT" else "9875")


def test_get_env_automatically_refreshes_config_changed_by_another_process(tmp_path, monkeypatch):
    """Config written by a separate setup command is visible on the next lookup."""
    config = tmp_path / "config"
    replacement = tmp_path / "config.new"
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)

    config.write_text("DASHSCOPE_BASE_URL=https://first.example/v1\n", encoding="utf-8")
    assert env_config.get_env("DASHSCOPE_BASE_URL") == "https://first.example/v1"

    replacement.write_text("DASHSCOPE_BASE_URL=https://second.example/v1\n", encoding="utf-8")
    replacement.replace(config)
    assert env_config.get_env("DASHSCOPE_BASE_URL") == "https://second.example/v1"


def test_environment_override_does_not_need_config_refresh(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text("DASHSCOPE_BASE_URL=https://config.example/v1\n", encoding="utf-8")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(config))
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://environment.example/v1")

    assert env_config.get_env("DASHSCOPE_BASE_URL", refresh_config=True) == "https://environment.example/v1"
