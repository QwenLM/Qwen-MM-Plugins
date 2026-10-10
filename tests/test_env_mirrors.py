"""Keep standalone skills and installed servers on the same configuration contract."""

import importlib.util
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts.sync_env_readers import mirror_paths, sync

_ROOT = Path(__file__).resolve().parents[1]
_MIRRORS = mirror_paths(_ROOT)
_READERS = [_ROOT / "src/shared/env.py", *_MIRRORS]
_VAR = "QMP_TEST_ENV_MIRROR"


def test_standalone_readers_are_current():
    assert sync(_ROOT) == 0


def test_sync_repairs_only_the_mirrored_region(tmp_path):
    source = tmp_path / "src/shared/env.py"
    mirror = tmp_path / "src/capabilities/example/skill/env_config.py"
    source.parent.mkdir(parents=True)
    mirror.parent.mkdir(parents=True)
    source.write_text((_ROOT / "src/shared/env.py").read_text(encoding="utf-8"), encoding="utf-8")
    original = _MIRRORS[0].read_text(encoding="utf-8") + "\nPLUGIN_SETTING = 'keep'\n"
    mirror.write_text(original.replace("def get_env(", "def stale_get_env("), encoding="utf-8")

    assert sync(tmp_path) == 1
    assert sync(tmp_path, write=True) == 0
    assert mirror.read_text(encoding="utf-8") == original


@pytest.fixture(params=_READERS, ids=lambda p: p.relative_to(_ROOT).parts[2])
def reader(request, tmp_path, monkeypatch):
    monkeypatch.setenv("QWEN_MM_CONFIG", str(tmp_path / "config"))
    monkeypatch.delenv(_VAR, raising=False)
    spec = importlib.util.spec_from_file_location("_test_env_reader", request.param)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    original_stat = config.stat()
    replacement.write_text(f"{_VAR}=other\n", encoding="utf-8")
    # Size and mtime alone miss an editor's atomic replacement that preserves timestamps.
    os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    replacement.replace(config)
    assert reader.get_env(_VAR) == "other"


def test_reader_observes_in_place_edits_even_with_restored_mtime(reader, tmp_path):
    config = tmp_path / "config"
    config.write_text(f"{_VAR}=first\n", encoding="utf-8")
    assert reader.get_env(_VAR) == "first"
    original_stat = config.stat()
    config.write_text(f"{_VAR}=other\n", encoding="utf-8")
    os.utime(config, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
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


def test_reader_reuses_unchanged_cache_and_can_force_a_read(reader, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text(f"{_VAR}=first\n", encoding="utf-8")
    parse = Mock(wraps=reader._parse_config)
    monkeypatch.setattr(reader, "_parse_config", parse)
    assert reader.get_env(_VAR) == "first"
    assert reader.get_env(_VAR) == "first"
    assert reader.get_env("QMP_MISSING_SETTING", "default") == "default"
    assert parse.call_count == 1
    # Simulate a filesystem reporting unchanged metadata after an edit.
    stamp = reader._config_file_stamp(str(config))
    monkeypatch.setattr(reader, "_config_file_stamp", lambda _: stamp)
    config.write_text(f"{_VAR}=second\n", encoding="utf-8")
    assert reader.get_env(_VAR) == "first"
    assert reader.get_env(_VAR, refresh_config=True) == "second"
    assert reader.get_env(_VAR) == "second"
    assert parse.call_count == 2


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


def test_concurrent_readers_observe_each_file_version(reader, tmp_path):
    config = tmp_path / "config"
    barrier = threading.Barrier(8)

    def read(_):
        barrier.wait(timeout=10)
        return reader.get_env(_VAR)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for expected in ("first", "second"):
            config.write_text(f"{_VAR}={expected}\n", encoding="utf-8")
            assert list(pool.map(read, range(8))) == [expected] * 8


def test_replacement_during_read_is_observed_on_next_lookup(reader, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.write_text(f"{_VAR}=first\n", encoding="utf-8")
    parse = reader._parse_config
    replaced = False

    def replace_after_read(text):
        nonlocal replaced
        if not replaced:
            replacement = tmp_path / "replacement"
            replacement.write_text(f"{_VAR}=second\n", encoding="utf-8")
            replacement.replace(config)
            replaced = True
        return parse(text)

    monkeypatch.setattr(reader, "_parse_config", replace_after_read)
    assert reader.get_env(_VAR) == "first"
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


@pytest.mark.parametrize("value,expected", [(" 9876 ", 9876), ("", 9875)])
def test_reader_plain_integer_default(reader, monkeypatch, value, expected):
    monkeypatch.setenv(_VAR, value)
    assert reader.get_int_env(_VAR, 9875) == expected


@pytest.mark.parametrize("value,default,expected", [(" On ", False, True), (" OFF ", True, False)])
def test_reader_boolean_default(reader, monkeypatch, value, default, expected):
    monkeypatch.setenv(_VAR, value)
    assert reader.get_bool_env(_VAR, default) is expected


@pytest.mark.parametrize("path", _MIRRORS, ids=lambda p: p.relative_to(_ROOT).parts[2])
def test_standalone_export_contract(path, tmp_path):
    config = tmp_path / "config"
    config.write_text("QMP_TEST_EXPORT=file-value\nQMP_TEST_OVERRIDE=file-value\n", encoding="utf-8")
    env = {**os.environ, "QWEN_MM_CONFIG": str(config), "QMP_TEST_OVERRIDE": ""}
    env.pop("QMP_TEST_EXPORT", None)
    result = subprocess.run(
        [sys.executable, "-I", str(path)], env=env, capture_output=True, text=True, check=True, timeout=10
    )
    exports = path.relative_to(_ROOT).parts[2] in {"video-memory", "omni-memory"}
    assert result.stdout == ("QMP_TEST_EXPORT=file-value\n" if exports else "")
    assert result.stderr == ""


@pytest.mark.parametrize(
    "kind,value", [("int", "invalid"), ("int", "15 MiB"), ("bool", "maybe"), ("float", "nan"), ("float", "inf")]
)
def test_every_reader_rejects_explicit_invalid_values(reader, tmp_path, monkeypatch, kind, value):
    for source in ("file", "environment"):
        if source == "file":
            (tmp_path / "config").write_text(f"{_VAR}={value}\n")
        else:
            monkeypatch.setenv(_VAR, value)
        with pytest.raises(reader.ConfigurationError, match=_VAR) as error:
            getattr(reader, f"get_{kind}_env")(_VAR)
        assert value not in str(error.value)


def test_catalog_defaults_are_shared_by_all_readers(reader, monkeypatch):
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
