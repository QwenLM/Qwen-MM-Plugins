"""Render source code files as markdown-fenced text blocks."""

from __future__ import annotations

import os
from typing import Any

_EXT_TO_LANG = {
    ".css": "css",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "jsx",
    ".tsx": "tsx",
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".sql": "sql",
    ".md": "markdown",
    ".toml": "toml",
    ".ini": "ini",
    ".lua": "lua",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".R": "r",
    ".dart": "dart",
    ".tex": "latex",
}

MAX_LINES = 500


def render(path: str, **opts: Any) -> list:
    ext = os.path.splitext(path)[1].lower()
    lang = _EXT_TO_LANG.get(ext, "")

    lines = [""]
    total_lines = 0
    ends_with_newline = True
    with open(path, "r", errors="replace") as f:
        while chunk := f.read(4096):
            chunk_lines = chunk.count("\n")
            if total_lines < MAX_LINES:
                if total_lines + chunk_lines < MAX_LINES:
                    lines.append(chunk)
                else:
                    end = 0
                    for _ in range(MAX_LINES - total_lines):
                        end = chunk.index("\n", end) + 1
                    lines.append(chunk[:end])
            total_lines += chunk_lines
            ends_with_newline = chunk.endswith("\n")
    total_lines += not ends_with_newline

    truncated = total_lines > MAX_LINES

    lang_label = lang.capitalize() if lang else ext.lstrip(".")
    summary = f"File: {path} | {lang_label} | {total_lines} lines"

    lines[0] = f"{summary}\n\n```{lang}\n"
    lines.append("\n```")
    if truncated:
        lines.append(f"\n\n... ({total_lines - MAX_LINES} more lines truncated)")

    return [{"type": "text", "text": "".join(lines)}]
