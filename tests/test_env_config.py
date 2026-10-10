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


@pytest.mark.parametrize("value,expected", [("1", True), ("0", False), (" On ", True), ("FALSE", False)])
def test_bool_env_normalizes_values(monkeypatch, value, expected):
    monkeypatch.setenv(_BOOL_VAR, value)
    assert get_bool_env(_BOOL_VAR) is expected


@pytest.mark.parametrize(
    "value,expected",
    [(None, 9876), ("   ", 9876), ("  5000  ", 5000)],
)
def test_int_env_valid_or_blank(monkeypatch, value, expected):
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


@pytest.fixture
def reader(tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv(_VAR, raising=False)
    return env_config


def test_reader_parses_config_and_blank_winning_values_use_default(reader, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text(
        f"# comment\nexport {_VAR}='first'\n{_VAR} = \"last=value with spaces\"\n",
        encoding="utf-8",
    )
    assert reader.get_env(_VAR, "default") == "last=value with spaces"
    monkeypatch.setenv(_VAR, "")
    assert reader.get_env(_VAR, "default") == "default"
    monkeypatch.delenv(_VAR)
    config.write_text(f"{_VAR}=\n", encoding="utf-8")
    assert reader.get_env(_VAR, "default", refresh_config=True) == "default"


def test_reader_refreshes_atomically_replaced_config(reader, tmp_path):
    config = tmp_path / "config"
    replacement = tmp_path / "replacement"
    config.write_text(f"{_VAR}=first\n", encoding="utf-8")
    assert reader.get_env(_VAR) == "first"
    replacement.write_text(f"{_VAR}=other\n", encoding="utf-8")
    replacement.replace(config)
    assert reader.get_env(_VAR) == "other"


def test_reader_observes_creation_deletion_and_location_changes(reader, tmp_path, monkeypatch):
    config = tmp_path / "config"
    assert reader.get_env(_VAR, "missing") == "missing"
    config.write_text(f"{_VAR}=created\n", encoding="utf-8")
    assert reader.get_env(_VAR) == "created"
    config.unlink()
    assert reader.get_env(_VAR, "deleted") == "deleted"
    other = tmp_path / "other"
    other.write_text(f"{_VAR}=moved\n", encoding="utf-8")
    monkeypatch.setenv("QWEN_MM_CONFIG", str(other))
    assert reader.get_env(_VAR) == "moved"
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("QWEN_MM_CONFIG")
    config.write_text(f"{_VAR}=directory\n", encoding="utf-8")
    assert reader.get_env(_VAR) == "directory"


def test_environment_override_stays_authoritative_during_file_edits(reader, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text(f"{_VAR}=first\n", encoding="utf-8")
    assert reader.get_env(_VAR) == "first"
    monkeypatch.setenv(_VAR, "environment")
    config.write_text(f"{_VAR}=second\n", encoding="utf-8")
    assert reader.get_env(_VAR, refresh_config=True) == "environment"
    monkeypatch.setenv(_VAR, "")
    assert reader.get_env(_VAR) is None
    monkeypatch.delenv(_VAR)
    assert reader.get_env(_VAR) == "second"


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid_utf8"])
def test_reader_only_uses_defaults_for_missing_config(reader, tmp_path, kind):
    config = tmp_path / "config"
    if kind == "directory":
        config.mkdir()
    elif kind == "invalid_utf8":
        config.write_bytes(b"\xff")
    if kind == "missing":
        assert reader.get_env(_VAR, "default") == "default"
    else:
        with pytest.raises(reader.ConfigurationError, match="readable UTF-8"):
            reader.get_env(_VAR, "default")


def test_reader_resolves_config_location_precedence(reader, tmp_path, monkeypatch):
    directory = tmp_path / "settings"
    monkeypatch.setenv("QWEN_MM_CONFIG_DIR", str(directory))
    assert reader.config_file() == str(tmp_path / "config")
    monkeypatch.delenv("QWEN_MM_CONFIG")
    assert reader.config_file() == str(directory / "config")
    monkeypatch.delenv("QWEN_MM_CONFIG_DIR")
    assert reader.config_file() == str(Path.home() / ".qwen-mm-plugins/config")


@pytest.mark.parametrize(
    "kind,value", [("int", "15 MiB"), ("int", "1.5"), ("bool", "maybe"), ("float", "nan"), ("float", "inf")]
)
def test_typed_readers_reject_explicit_invalid_values(reader, tmp_path, monkeypatch, caplog, kind, value):
    for source in ("file", "environment"):
        if source == "file":
            (tmp_path / "config").write_text(f"{_VAR}={value}\n")
        else:
            monkeypatch.setenv(_VAR, value)
        with pytest.raises(reader.ConfigurationError, match=_VAR) as error:
            getattr(reader, f"get_{kind}_env")(_VAR)
        assert value not in str(error.value)
        assert value not in caplog.text


def test_catalog_defaults_override_caller_fallbacks(reader, monkeypatch):
    for key, _, _, default, _ in reader.config_catalog():
        monkeypatch.delenv(key, raising=False)
        if default:
            assert reader.get_env(key) == default
            # A plugin cannot accidentally redefine an ordinary field's default.
            assert reader.get_env(key, "conflicting fallback") == default
    assert reader.get_int_env("QWEN_MM_CHAT_TIMEOUT") == 1800
    assert reader.get_bool_env("QWEN_MM_NATIVE_MODE") is True


@pytest.mark.parametrize("line", ["missing-equals", "=missing-key", "bad key=value", "KEY='unfinished"])
def test_malformed_config_is_an_error_and_can_be_repaired(reader, tmp_path, line):
    config = tmp_path / "config"
    config.write_text(f"# header\n{line}\n")
    with pytest.raises(reader.ConfigurationError, match="config file line 2") as error:
        reader.get_env(_VAR, "default")
    assert line not in str(error.value)
    config.write_text(f"{_VAR}=fixed\n")
    assert reader.get_env(_VAR) == "fixed"
