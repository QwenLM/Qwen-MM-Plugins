"""Lifecycle CLI: start/stop/status/logs/test-image/check (spec §8.3)."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys

from shared.env import config_dir

PID_FILE = "proxy.pid"
LOG_FILE = "proxy.log"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="qwen-mm-plugins-proxy")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start")
    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("logs")
    ti = sub.add_parser("test-image")
    ti.add_argument("path")
    ti.add_argument("--question", default=None)
    sub.add_parser("check")
    return parser.parse_args(argv)


def _pid_path() -> str:
    return os.path.join(config_dir(), PID_FILE)


def _log_path() -> str:
    return os.path.join(config_dir(), "logs", LOG_FILE)


def _write_pid() -> None:
    os.makedirs(config_dir(), exist_ok=True)
    with open(_pid_path(), "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def cmd_start(cfg) -> int:
    if os.path.exists(_pid_path()):
        print(f"already running (pid {open(_pid_path()).read().strip()})")
        return 1
    _write_pid()
    from .server import run_server

    server = run_server(cfg)
    print(f"qwen-mm-plugins-proxy listening on {cfg.bind_host}:{cfg.bind_port} (data) / {cfg.ui_port} (control)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        os.unlink(_pid_path())
    return 0


def cmd_stop() -> int:
    try:
        with open(_pid_path(), encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        print("not running")
        return 1
    if not _pid_running(pid):
        # 过期 pid（进程已死）残留：清理 pid 文件，避免 stop/start 卡在坏 pid 上
        try:
            os.unlink(_pid_path())
        except OSError:
            pass
        print("not running")
        return 1
    if not _terminate(pid):
        print(f"cannot stop {pid}")
        return 1
    try:
        os.unlink(_pid_path())
    except OSError:
        pass
    print(f"stopped {pid}")
    return 0


def _terminate(pid: int) -> bool:
    """终止进程。Windows 上 os.kill(pid, SIGTERM) 对失效 pid 抛 WinError 87，
    用 TerminateProcess 兜底（与 _pid_running 同源的 Windows 健壮性处理）。"""
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        if os.name == "nt":
            try:
                import ctypes

                PROCESS_TERMINATE = 0x0001
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    ok = ctypes.windll.kernel32.TerminateProcess(handle, 1)
                    ctypes.windll.kernel32.CloseHandle(handle)
                    return bool(ok)
            except Exception:
                return False
        return False


def _pid_running(pid: int) -> bool:
    """跨平台判断 PID 是否存活。Windows 上 os.kill(pid, 0) 抛 WinError 87 而非
    ProcessLookupError，需用 OpenProcess 复核，否则 status 永远误报 not running。"""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        return False


def cmd_status() -> int:
    try:
        with open(_pid_path(), encoding="utf-8") as f:
            pid = int(f.read().strip())
        if _pid_running(pid):
            print(f"running (pid {pid})")
            return 0
        print("not running")
        return 1
    except FileNotFoundError:
        print("not running")
        return 1


def cmd_logs() -> int:
    try:
        with open(_log_path(), encoding="utf-8") as f:
            sys.stdout.write("".join(f.readlines()[-50:]))
        return 0
    except FileNotFoundError:
        print("no log yet")
        return 1


def cmd_test_image(args, cfg) -> int:
    import base64
    import mimetypes

    from .ir import ImageBlock
    from .vlm import VLMClient

    try:
        with open(args.path, "rb") as f:
            data = base64.b64encode(f.read()).decode()
    except OSError as exc:
        print(f"cannot read {args.path}: {exc}")
        return 1
    media = mimetypes.guess_type(args.path)[0] or "image/png"
    client = VLMClient(cfg.vlm)
    img = ImageBlock(base64=data, media_type=media)
    try:
        t1 = client.describe(img, tier=1)
        t2 = client.describe(img, question=args.question, tier=2) if args.question else t1
    except Exception as exc:
        # VLMError 带 reason（TIMEOUT/HTTP/AUTH/PARSE/TRANSPORT）；mimo 瞬时 5xx/超时可能返回，
        # 只打 str(exc) 在空 body 时是无意义的 "VLM error:"，带上 reason 才有可能诊断。
        reason = getattr(exc, "reason", type(exc).__name__)
        print(f"VLM error [{reason}]: {exc}")
        return 1
    print("Tier1 (全面):\n" + t1 + "\n\nTier2 (聚焦):\n" + t2)
    return 0


def cmd_check(cfg) -> int:
    problems = []
    for port in (cfg.bind_port, cfg.ui_port):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                problems.append(f"port {port} already in use")
    if not cfg.vlm.api_key and not cfg.vlm.auto_local_ollama:
        problems.append("no VLM key configured and auto_local_ollama disabled")
    if not cfg.relays:
        problems.append("no relays configured")
    for p in problems:
        print(f"\u26a0 {p}")
    if not problems:
        print("check ok")
    return 1 if problems else 0


def _safe_stdio() -> None:
    """stdout/stderr 用 errors='replace'：Windows 控制台若是 GBK/cp936，mimo 描述里
    非编码字符（如 OCR 图标 ☼ U+263C）会让 print() 抛 UnicodeEncodeError 崩溃。
    这里只替换、不崩溃（保持终端原本的编码偏好）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _safe_stdio()
    args = parse_args(argv)
    # stop/status/logs 只读 PID/日志，不依赖配置——损坏的 proxy.json 不能锁死这些生命周期命令。
    if args.command in ("stop", "status", "logs"):
        return {"stop": cmd_stop, "status": cmd_status, "logs": cmd_logs}[args.command]()
    from .config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if args.command == "start":
        return cmd_start(cfg)
    if args.command == "test-image":
        return cmd_test_image(args, cfg)
    if args.command == "check":
        return cmd_check(cfg)
    return 1
