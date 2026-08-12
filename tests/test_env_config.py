"""Out-of-box robustness: a bad size/timeout config value must NOT crash the server.

`shared.env._int_env` parses the QWEN_MM_* size/timeout knobs at import time. A user who types a
human value like "15 MiB" (exactly what the `--setup` hint shows) — or plain garbage — must get a
sane value or fall back to the default, never a ValueError that aborts the whole MCP server before
the protocol handshake. These lock that in.
"""

from pathlib import Path

import pytest
from scripts.gen_env_docs import check, load_config_fields

from shared.env import _int_env

_VAR = "QMP_TEST_INT_ENV"
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
