"""Console entry point for the Qwen-MM-Plugins CUA MCP proxy."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_IMPORT = _PKG_DIR.name

# Run-from-source support, matching the other capability entry points.
if _IMPORT not in sys.modules:
    sys.path.insert(0, str(_PKG_DIR.parent))
    sys.path.insert(0, str(_PKG_DIR.parents[2]))


def main() -> None:
    package = import_module(_IMPORT)
    proxy = import_module(f"{_IMPORT}.proxy")
    __version__ = package.__version__
    check_system = proxy.check_system
    resolve_driver = proxy.resolve_driver
    run_proxy = proxy.run_proxy

    argv = sys.argv[1:]
    if "--version" in argv:
        print(__version__)
        return
    if "--check-system" in argv:
        print(check_system())
        return
    if "--setup" in argv:
        from shared.env import get_env, set_config

        current = get_env("QWEN_MM_CUA_DRIVER_PATH") or ""
        prompt = "QWEN_MM_CUA_DRIVER_PATH (blank = auto-detect; '-' = clear)"
        try:
            value = input(f"{prompt}{f' [{current}]' if current else ''}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naborted.")
            return
        if value == "-":
            from shared.env import del_config

            print(f"✓ removed QWEN_MM_CUA_DRIVER_PATH → {del_config(['QWEN_MM_CUA_DRIVER_PATH'])}")
        elif value:
            print(f"✓ wrote QWEN_MM_CUA_DRIVER_PATH → {set_config({'QWEN_MM_CUA_DRIVER_PATH': value})}")
        else:
            print("unchanged.")
        return
    if "--set" in argv:
        from shared.env import set_config

        values = {
            key: value for arg in argv for key, sep, value in [arg.partition("=")] if sep and not key.startswith("-")
        }
        if not values:
            print("usage: qwen-mm-plugins-cua --set KEY=VALUE [KEY=VALUE …]")
            raise SystemExit(2)
        print(f"✓ wrote {', '.join(sorted(values))} → {set_config(values)}")
        return
    if "--unset" in argv:
        from shared.env import del_config

        keys = [arg for arg in argv if not arg.startswith("-") and "=" not in arg]
        if not keys:
            print("usage: qwen-mm-plugins-cua --unset KEY [KEY …]")
            raise SystemExit(2)
        print(f"✓ removed {', '.join(sorted(keys))} → {del_config(keys)}")
        return
    if "--help" in argv or "-h" in argv or sys.stdin.isatty():
        print(
            "qwen-mm-plugins-cua — MCP proxy for the locally installed Cua Driver\n\n"
            "Usage: qwen-mm-plugins-cua [--version | --check-system | --setup | --set KEY=VALUE … | "
            "--unset KEY … | --help]\n\n"
            "The proxy resolves QWEN_MM_CUA_DRIVER_PATH, CUA_DRIVER_PATH, the default installer "
            "location (~/.local/bin), the macOS app bundle, then PATH."
        )
        return
    try:
        driver = resolve_driver()
    except RuntimeError as exc:
        print(f"qwen-mm-plugins-cua: {exc}", file=sys.stderr)
        raise SystemExit(127) from exc
    if driver is None:
        print(check_system(), file=sys.stderr)
        raise SystemExit(127)
    raise SystemExit(run_proxy(driver))


if __name__ == "__main__":
    main()
