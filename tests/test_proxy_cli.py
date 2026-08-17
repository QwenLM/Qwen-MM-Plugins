"""Proxy capability: lifecycle CLI (start/stop/status/logs/test-image/check)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from qwen_mm_plugins_proxy.cli import parse_args

# ── parse_args tests ────────────────────────────────────────────────────────


def test_parse_args_test_image():
    args = parse_args(["test-image", "/tmp/a.png", "--question", "红字说了什么"])
    assert args.command == "test-image"
    assert args.path == "/tmp/a.png"
    assert args.question == "红字说了什么"


def test_parse_args_status():
    assert parse_args(["status"]).command == "status"


def test_parse_args_start():
    assert parse_args(["start"]).command == "start"


def test_parse_args_stop():
    assert parse_args(["stop"]).command == "stop"


def test_parse_args_logs():
    assert parse_args(["logs"]).command == "logs"


def test_parse_args_check():
    assert parse_args(["check"]).command == "check"


def test_parse_args_test_image_no_question():
    args = parse_args(["test-image", "/tmp/b.jpg"])
    assert args.command == "test-image"
    assert args.path == "/tmp/b.jpg"
    assert args.question is None


def test_unknown_command_exits_nonzero():
    with pytest.raises(SystemExit):
        parse_args(["frobnicate"])


# ── cmd_status tests (no server) ───────────────────────────────────────────


def test_cmd_status_not_running(tmp_path):
    from qwen_mm_plugins_proxy.cli import cmd_status

    with patch("qwen_mm_plugins_proxy.cli._pid_path", return_value=str(tmp_path / "proxy.pid")):
        assert cmd_status() == 1


def test_cmd_stop_not_running(tmp_path):
    from qwen_mm_plugins_proxy.cli import cmd_stop

    with patch("qwen_mm_plugins_proxy.cli._pid_path", return_value=str(tmp_path / "proxy.pid")):
        assert cmd_stop() == 1


def test_cmd_status_running(tmp_path):
    from qwen_mm_plugins_proxy.cli import cmd_status

    pid_file = tmp_path / "proxy.pid"
    pid_file.write_text("999999999")
    # Process 999999999 does not exist -> ProcessLookupError -> "not running"
    with patch("qwen_mm_plugins_proxy.cli._pid_path", return_value=str(pid_file)):
        assert cmd_status() == 1


def test_cmd_stop_stale_pid(tmp_path):
    from qwen_mm_plugins_proxy.cli import cmd_stop

    pid_file = tmp_path / "proxy.pid"
    pid_file.write_text("999999999")
    # kill of nonexistent pid -> ProcessLookupError -> "not running"
    with patch("qwen_mm_plugins_proxy.cli._pid_path", return_value=str(pid_file)):
        assert cmd_stop() == 1


# ── cmd_logs tests ──────────────────────────────────────────────────────────


def test_cmd_logs_no_log(tmp_path):
    from qwen_mm_plugins_proxy.cli import cmd_logs

    with patch("qwen_mm_plugins_proxy.cli._log_path", return_value=str(tmp_path / "nonexistent.log")):
        assert cmd_logs() == 1


def test_cmd_logs_reads_tail(tmp_path):
    from qwen_mm_plugins_proxy.cli import cmd_logs

    log_file = tmp_path / "proxy.log"
    log_file.write_text("line1\nline2\nline3\n")
    with patch("qwen_mm_plugins_proxy.cli._log_path", return_value=str(log_file)):
        assert cmd_logs() == 0


# ── cmd_check tests (no relay, no port occupied) ────────────────────────────


def test_cmd_check_warns_no_relays():
    from qwen_mm_plugins_proxy.cli import cmd_check
    from qwen_mm_plugins_proxy.config import ProxyConfig

    cfg = ProxyConfig()
    # No relays -> problem reported, exit 1
    with patch("socket.socket") as mock_sock:
        mock_instance = mock_sock.return_value.__enter__.return_value
        mock_instance.connect_ex.return_value = 1  # port free
        assert cmd_check(cfg) == 1


def test_cmd_check_port_in_use():
    from qwen_mm_plugins_proxy.cli import cmd_check
    from qwen_mm_plugins_proxy.config import ProxyConfig

    cfg = ProxyConfig()
    with patch("socket.socket") as mock_sock:
        mock_instance = mock_sock.return_value.__enter__.return_value
        mock_instance.connect_ex.return_value = 0  # port occupied
        # Both ports in use + no relays
        result = cmd_check(cfg)
        assert result == 1


def test_cmd_start_already_running(tmp_path):
    from qwen_mm_plugins_proxy.cli import cmd_start
    from qwen_mm_plugins_proxy.config import ProxyConfig

    pid_file = tmp_path / "proxy.pid"
    pid_file.write_text("42")
    cfg = ProxyConfig()
    with patch("qwen_mm_plugins_proxy.cli._pid_path", return_value=str(pid_file)):
        assert cmd_start(cfg) == 1


# ── main() dispatch tests ───────────────────────────────────────────────────


def test_main_dispatch_status(tmp_path):
    from qwen_mm_plugins_proxy.cli import main

    with patch("qwen_mm_plugins_proxy.cli._pid_path", return_value=str(tmp_path / "proxy.pid")):
        with patch("qwen_mm_plugins_proxy.config.load_config") as mock_cfg:
            from qwen_mm_plugins_proxy.config import ProxyConfig

            mock_cfg.return_value = ProxyConfig()
            assert main(["status"]) == 1  # no process running


def test_main_dispatch_stop(tmp_path):
    from qwen_mm_plugins_proxy.cli import main

    with patch("qwen_mm_plugins_proxy.cli._pid_path", return_value=str(tmp_path / "proxy.pid")):
        with patch("qwen_mm_plugins_proxy.config.load_config") as mock_cfg:
            from qwen_mm_plugins_proxy.config import ProxyConfig

            mock_cfg.return_value = ProxyConfig()
            assert main(["stop"]) == 1  # no process running


def test_main_dispatch_logs(tmp_path):
    from qwen_mm_plugins_proxy.cli import main

    with patch("qwen_mm_plugins_proxy.cli._log_path", return_value=str(tmp_path / "nonexistent.log")):
        with patch("qwen_mm_plugins_proxy.config.load_config") as mock_cfg:
            from qwen_mm_plugins_proxy.config import ProxyConfig

            mock_cfg.return_value = ProxyConfig()
            assert main(["logs"]) == 1


# ── cmd_test_image VLM error handling ──────────────────────────────────────


def test_cmd_test_image_vlm_error(tmp_path):
    """I6: VLM errors should be caught and reported cleanly, not traceback."""
    from qwen_mm_plugins_proxy.cli import cmd_test_image
    from qwen_mm_plugins_proxy.config import ProxyConfig

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG")
    cfg = ProxyConfig()
    args = parse_args(["test-image", str(img_file)])
    with patch("qwen_mm_plugins_proxy.vlm.VLMClient") as mock_cls:
        mock_client = mock_cls.return_value
        from qwen_mm_plugins_proxy.vlm import VLMError

        mock_client.describe.side_effect = VLMError("TIMEOUT", "timed out")
        assert cmd_test_image(args, cfg) == 1
