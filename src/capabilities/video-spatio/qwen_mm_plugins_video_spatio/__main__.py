"""Generic MCP server entry point — resolves package from directory name, delegates to mcp_framework."""

import sys
from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_IMPORT = _PKG_DIR.name

# Run-from-source: ensure this package and mcp_framework are importable.
if _IMPORT not in sys.modules:
    sys.path.insert(0, str(_PKG_DIR.parent))
    sys.path.insert(0, str(_PKG_DIR.parents[2]))


def main() -> None:
    from mcp_framework import run_main

    run_main(_IMPORT)


if __name__ == "__main__":
    main()
