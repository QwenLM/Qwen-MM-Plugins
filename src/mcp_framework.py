"""Shared MCP-server framework for Qwen-MM-Plugins, built on the SDK's FastMCP.

A server is just its tool subpackages plus a thin `__init__.py` (`__version__`, the discovered
`SPECS`, a few optional hooks). This module provides what every server would otherwise duplicate:

  - build_registry(import_name, tool_packages) — auto-discover TOOL+handle modules
  - serve(...)                                  — bridge tools onto FastMCP + run stdio
  - run_main(import_name)                        — console / `python3 <dir>` entry dispatcher
  - tool_schema(model)                          — Pydantic model -> advertised inputSchema

Each tool module exports `TOOL = {"name", "description", "args": <PydanticModel>}` + `handle(dict)`.
Depends only on the `mcp` SDK (bundles FastMCP + pydantic) + anyio, so servers stay independent.
"""

from __future__ import annotations

__version__ = "1.0.1"  # distribution/release-train version; plugin versions are per capability

import asyncio
import importlib
import importlib.util
import inspect
import json
import logging
import os
import pkgutil
import shutil
import signal
import sys
import warnings
from typing import Annotated

import anyio
from pydantic.json_schema import GenerateJsonSchema

__all__ = [
    "build_registry",
    "serve",
    "run_main",
    "tool_schema",
    "system_report",
    "system_startup_warnings",
]


# Schema: Pydantic model -> the JSON inputSchema advertised to the model.
class _ToolSchemaGen(GenerateJsonSchema):
    """Emit LLM-clean tool schemas straight from pydantic, no post-hoc rewriting: drop auto-generated
    `title`s (field- and class-level), unwrap `Optional`'s null branch, and omit `default: null`.
    `field_title_should_be_set`/`nullable_schema`/`default_schema` are documented override points;
    `_update_class_schema` (nested class titles) is private with no public equivalent, so the
    tool_schema tests assert the output stays title-free across pydantic upgrades. Paired with
    union_format='primitive_type_array' so `float | str` -> {"type": ["number", "string"]}."""

    def field_title_should_be_set(self, schema) -> bool:
        return False

    def _update_class_schema(self, json_schema, *args, **kwargs) -> None:
        super()._update_class_schema(json_schema, *args, **kwargs)
        json_schema.pop("title", None)

    def nullable_schema(self, schema) -> dict:
        return self.generate_inner(schema["schema"])  # Optional[X] -> X, drop the anyOf null branch

    def default_schema(self, schema) -> dict:
        js = super().default_schema(schema)
        if js.get("default", ...) is None:
            js.pop("default", None)  # omit `default: null`; real defaults are kept
        return js


def tool_schema(model) -> dict:
    """Build the advertised MCP inputSchema from a Pydantic model: generated title-free with
    Optional/`default: null` already stripped (see _ToolSchemaGen), then inline `$ref`/`$defs`
    and drop the top-level model description. Per-property `description`s are kept."""
    schema = _inline_defs(model.model_json_schema(schema_generator=_ToolSchemaGen, union_format="primitive_type_array"))
    schema.pop("description", None)
    return schema


def _inline_defs(schema: dict) -> dict:
    """Replace every `#/$defs/*` `$ref` with the referenced definition (inlined) and drop the
    `$defs` block."""
    defs = schema.pop("$defs", None)
    if not defs:
        return schema

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = defs[ref.split("/")[-1]]
                sibling = {k: v for k, v in node.items() if k != "$ref"}
                return walk({**target, **sibling})
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


# Discovery: scan subpackages for tool modules (TOOL dict + handle fn).
class ToolSpec:
    """One discovered tool: its advertised schema, Pydantic args model, and handler."""

    __slots__ = ("name", "description", "input_schema", "args_model", "handle")

    def __init__(self, name, description, input_schema, args_model, handle):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.args_model = args_model
        self.handle = handle

    @property
    def meta(self) -> dict:
        return {"name": self.name, "description": self.description, "inputSchema": self.input_schema}


def build_registry(import_name: str, tool_packages: list[str]):
    """Auto-discover tool modules (exporting TOOL + handle) under the given subpackages of
    `import_name`. Returns (specs, get_handler, list_tools): a list of ToolSpec, a
    `get_handler(name) -> handle | None`, and a `list_tools() -> [tool meta dict]`. Raises on
    a module that exports only one of TOOL/handle (a typo that would otherwise vanish
    silently) or on a duplicate tool name (an ambiguous registry)."""
    specs: list[ToolSpec] = []
    seen: dict[str, str] = {}
    for pkg_name in tool_packages:
        pkg = importlib.import_module(f"{import_name}.{pkg_name}")
        for info in pkgutil.iter_modules(pkg.__path__):
            full = f"{import_name}.{pkg_name}.{info.name}"
            mod = importlib.import_module(full)
            has_tool, has_handle = hasattr(mod, "TOOL"), hasattr(mod, "handle")
            if has_tool != has_handle:
                missing, present = ("handle", "TOOL") if has_tool else ("TOOL", "handle")
                raise RuntimeError(
                    f"{full} exports {present} but not {missing}: a tool module must export both "
                    "(or neither, for a helper module)."
                )
            if not has_tool:
                continue
            spec = _spec_from_module(mod)
            if spec.name in seen:
                raise RuntimeError(f"duplicate tool name {spec.name!r}: defined in {seen[spec.name]} and {full}.")
            seen[spec.name] = full
            specs.append(spec)
    handlers = {s.name: s.handle for s in specs}

    def list_tools() -> list[dict]:
        return [s.meta for s in specs]

    return specs, handlers.get, list_tools


def _spec_from_module(mod) -> ToolSpec:
    t = mod.TOOL
    model = t["args"]
    return ToolSpec(t["name"], t.get("description", ""), tool_schema(model), model, mod.handle)


# Runtime: bridge specs onto FastMCP and run over stdio.
def _to_content_block(block: dict):
    """Convert a handler's raw content dict into an SDK content block (text/image;
    anything else falls back to JSON text so a stray block can't crash a call)."""
    import mcp.types as types

    btype = block.get("type")
    if btype == "image" and "data" in block:
        # Codex MCP image-detail extension ("original" instead of its default "high"):
        # https://github.com/openai/codex/blob/44cb66e4edc061d39ae38de949b47f6f94416553/codex-rs/protocol/src/models.rs#L2106-L2187
        return types.ImageContent(
            type="image",
            data=block["data"],
            mimeType=block.get("mimeType", "image/jpeg"),
            _meta={"codex/imageDetail": "original"},
        )
    if btype == "text":
        return types.TextContent(type="text", text=block.get("text", ""))
    return types.TextContent(type="text", text=json.dumps(block, ensure_ascii=False))


async def _run_handle(handle, arguments: dict):
    raw = await anyio.to_thread.run_sync(lambda: handle(arguments))
    return [_to_content_block(b) for b in raw]


def _make_wrapper(spec: ToolSpec):
    """Synthesize the async tool fn FastMCP registers: its signature (from the Pydantic
    args model) drives schema-generation + validation; its body dumps the validated model
    to a plain dict, runs the handle off the event loop, and converts the result."""
    handle = spec.handle
    model = spec.args_model
    params = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            default=(inspect.Parameter.empty if field.is_required() else field.default),
            annotation=Annotated[field.annotation, field],
        )
        for name, field in model.model_fields.items()
    ]

    async def wrapper(**kwargs):
        # validated model -> plain dict for the handle(dict) API
        return await _run_handle(handle, model(**kwargs).model_dump(exclude_none=True))

    wrapper.__signature__ = inspect.Signature(params)
    wrapper.__name__ = spec.name
    return wrapper


def serve(server_name, version, specs, *, transport=None, on_start=None):
    """Run an MCP stdio server bridging discovered tool specs onto FastMCP.

    FastMCP validates inputs against each spec's args model; we override the advertised schema with
    our normalized `tool_schema`. `transport` is an optional (read, write) context-manager factory
    (defaults to the SDK stdio_server; the vision server passes its streaming writer); `on_start`
    runs once after the transport opens."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.stdio import stdio_server

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    # Ignore SIGPIPE: a client disconnect surfaces as an exception (handled in run_main),
    # not a process kill. Unix-only.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    mcp = FastMCP(server_name)
    mcp._mcp_server.version = version  # reported in the initialize handshake
    with warnings.catch_warnings():
        # wrapper defaults aren't JSON-serializable; we override .parameters below, so silence it.
        warnings.filterwarnings("ignore", message="Default value .* is not JSON serializable")
        for spec in specs:
            mcp.add_tool(_make_wrapper(spec), name=spec.name, description=spec.description, structured_output=False)
            mcp._tool_manager.get_tool(spec.name).parameters = spec.input_schema

    low = mcp._mcp_server

    async def _run():
        tctx = transport or stdio_server
        async with tctx() as (read, write):
            if on_start is not None:
                on_start()
            await low.run(read, write, low.create_initialization_options())

    asyncio.run(_run())


# System dependencies: a declarative table -> --check-system report + startup warnings.
# Some capabilities need a *system* tool pip/uv can't install (ffmpeg, blender, libreoffice, latex,
# a Playwright browser). A capability declares a SYSTEM_DEPS list (+ optional SYSTEM_DEPS_NOTE); the
# framework renders --check-system (run_main) and warns at startup about tools missing while their
# Python extra is installed. Each entry — required: label, tools, hint; the rest are optional and
# default to the common case (a system binary with no Python gating, warned at startup when missing):
#   label   (required) human-readable capability the tool powers
#   tools   (required) system binaries (any-of: present if ANY resolves on PATH)
#   hint    (required) install command
#   extra   pip group that pulls its Python side; omit / None = core, always relevant (shown [core])
#   probe   import name to detect that Python side; omit / None = no Python dep (entry always active)
#   startup omit / True = warn at startup when missing; False = report-only (--check-system only)
def _tool_present(tool: str) -> bool:
    if tool == "__playwright_chromium__":
        # The browser lives in the playwright cache, not on PATH — can't detect it, so report-only.
        return False
    return shutil.which(tool) is not None


def _extra_installed(probe) -> bool:
    """True if the entry's Python side is importable (or it has no Python dep)."""
    return probe is None or importlib.util.find_spec(probe) is not None


def _entry_ok(dep: dict) -> bool:
    return any(_tool_present(t) for t in dep["tools"])


def system_report(deps, *, note: str = "") -> str:
    """Render `--check-system` for a SYSTEM_DEPS table, scoped to installed extras.

    ✓/✗ per entry whose Python extra is importable (plus entries with no Python dep); entries whose
    extra isn't installed collapse into one trailing line. `note` is an optional footer."""
    if not deps:
        return "No system tools required."
    lines = ["System-tool dependency check (pip/uv cannot install these):", ""]
    dormant: list[str] = []
    for dep in deps:
        if not _extra_installed(dep.get("probe")):
            if dep.get("extra"):
                dormant.append(dep["extra"])
            continue
        ok = _entry_ok(dep)
        mark = "✓" if ok else "✗"
        extra = f" [{dep['extra']}]" if dep.get("extra") else " [core]"
        lines.append(f"  {mark} {dep['label']}{extra}")
        if not ok:
            real = [t for t in dep["tools"] if t != "__playwright_chromium__"]
            lines.append(f"      needs: {' or '.join(real) or 'playwright chromium'}")
            lines.append(f"      install: {dep['hint']}")
    if dormant:
        lines.append("")
        lines.append(f"  (extras not installed — add to enable: {', '.join(sorted(set(dormant)))})")
    if note:
        lines.append("")
        lines.append(note)
    return "\n".join(lines)


def system_startup_warnings(deps) -> list[str]:
    """Missing system tools whose Python extra IS installed — what the user will hit at runtime.
    Skips startup=False entries."""
    out = []
    for dep in deps or []:
        if not dep.get("startup", True):
            continue
        if _extra_installed(dep.get("probe")) and not _entry_ok(dep):
            out.append(f"{dep['label']} — install: {dep['hint']}")
    return out


def usage(import_name: str, note: str = "", *, launchable: bool = False) -> str:
    """Standard --help / no-tty text for a server, derived from its entry name.
    `note` is the server-specific tail (install hints, required env vars, …).
    `launchable` adds the --launch-app line for capabilities that can start their own app."""
    entry = import_name.replace("_", "-")
    launch_opt = f"Usage: {entry} [--version | --help | --check-system | --setup | --set KEY=VALUE … | --unset KEY …"
    launch_opt += " | --launch-app]\n" if launchable else "]\n"
    text = (
        f"{entry} — MCP server (stdio transport)\n\n"
        f"{launch_opt}"
        "  --check-system   report system tools (ffmpeg/blender/…) pip can't install, + config status\n"
        "  --setup          interactively write the full config to ~/.qwen-mm-plugins/config\n"
        "  --set KEY=VALUE  non-interactively write config entries (for automation)\n"
        "  --unset KEY …    non-interactively remove config entries\n"
    )
    if launchable:
        text += (
            "  --launch-app     bring up the live app (with the bundled addon) this server talks to;\n"
            "                   pass --launch-app --help for its options\n"
        )
    return f"{text}\n{note}" if note else text


def config_report(entry: str) -> str:
    """`--check-system` tail: where the user config file is + whether the API key resolves."""
    from shared.env import config_file, get_env

    path = config_file()
    lines = [f"User config: {path}" + ("" if os.path.exists(path) else f"  (none yet — `{entry} --setup`)")]
    key = get_env("DASHSCOPE_API_KEY")
    if key:
        src = "environment" if os.environ.get("DASHSCOPE_API_KEY") else "config file"
        masked = f"{key[:5]}…{key[-2:]}" if len(key) > 9 else "set"
        lines.append(f"  ✓ DASHSCOPE_API_KEY ({src}): {masked}")
    else:
        lines.append(f"  ✗ DASHSCOPE_API_KEY not set — run `{entry} --setup`")
    return "\n".join(lines)


def _interactive_setup(entry: str) -> None:
    """Prompt for every catalog field, grouped, and merge the answers into the user config file.

    Iterates shared.env.CONFIG_FIELDS (the one declarative list) so new vars need no new prompt
    code. Per field: blank keeps the current value, `-` clears it (removed, not written empty)."""
    import getpass

    from shared.env import CONFIG_FIELDS, config_file, del_config, get_env, set_config

    print(f"{entry} setup → {config_file()}")
    print("Enter a value, blank to keep the current one, or '-' to clear it. Ctrl-C to abort.")
    values: dict[str, str | None] = {}
    cleared: list[str] = []
    group = None
    try:
        for key, secret, grp, default, desc in CONFIG_FIELDS:
            if grp != group:
                group = grp
                print(f"\n— {grp} —")
            cur = get_env(key)
            if cur:
                shown = "set" if secret else cur
            elif default:
                shown = f"default: {default}"
            else:
                shown = ""
            hint = f" [{shown}]" if shown else ""
            prompt = f"  {key} ({desc}){hint}: "
            v = (getpass.getpass(prompt) if secret else input(prompt)).strip()
            if not v:
                continue
            if v == "-":
                cleared.append(key)
            else:
                values[key] = v
    except (EOFError, KeyboardInterrupt):
        print("\naborted.")
        return
    if not values and not cleared:
        print("\nNothing changed.")
        return
    path = set_config(values) if values else config_file()
    if cleared:
        path = del_config(cleared)
    done = sorted(values) + [f"-{k}" for k in cleared]
    print(f"\n✓ saved {', '.join(done)} → {path}")


def _check_system_text(pkg) -> str:
    """`--check-system` text: an explicit check_system() override on the package wins; else
    render its declarative SYSTEM_DEPS (+ optional SYSTEM_DEPS_NOTE footer); else nothing needed."""
    check = getattr(pkg, "check_system", None)
    if check is not None:
        return check()
    deps = getattr(pkg, "SYSTEM_DEPS", None)
    if deps is not None:
        return system_report(deps, note=getattr(pkg, "SYSTEM_DEPS_NOTE", ""))
    return "No system tools required."


def run_main(import_name: str) -> None:
    """Console / `python3 <dir>` entry: --version / --check-system / --setup / --help / serve.

    Reads config off the package: required __version__, SPECS; optional SYSTEM_DEPS (+
    SYSTEM_DEPS_NOTE), check_system() override, on_start(), transport, USAGE_NOTE."""
    pkg = importlib.import_module(import_name)
    argv = sys.argv[1:]
    entry = import_name.replace("_", "-")
    if "--version" in argv:
        print(pkg.__version__)
        return
    if "--check-system" in argv:
        print(_check_system_text(pkg))
        print()
        print(config_report(entry))
        return
    if "--setup" in argv:
        _interactive_setup(entry)
        return
    if "--set" in argv:
        from shared.env import set_config

        pairs: dict[str, str] = {}
        for a in argv:
            k, sep, v = a.partition("=")
            if sep and not k.startswith("-"):
                pairs[k] = v
        if not pairs:
            print(f"usage: {entry} --set KEY=VALUE [KEY=VALUE …]")
            return
        print(f"✓ wrote {', '.join(sorted(pairs))} → {set_config(pairs)}")
        return
    if "--unset" in argv:
        from shared.env import del_config

        keys = [a for a in argv if not a.startswith("-") and "=" not in a]
        if not keys:
            print(f"usage: {entry} --unset KEY [KEY …]")
            return
        print(f"✓ removed {', '.join(sorted(keys))} → {del_config(keys)}")
        return
    if "--launch-app" in argv:
        launch = getattr(pkg, "launch_app", None)
        if launch is None:
            print(f"{import_name.replace('_', '-')} has no launchable app.", file=sys.stderr)
            sys.exit(2)
        rest = [a for a in argv if a != "--launch-app"]
        sys.exit(launch(rest) or 0)
    if "--help" in argv or "-h" in argv or sys.stdin.isatty():
        print(
            usage(import_name, getattr(pkg, "USAGE_NOTE", ""), launchable=getattr(pkg, "launch_app", None) is not None)
        )
        if not ({"--help", "-h"} & set(argv)):
            sys.exit(1)
        return

    log = logging.getLogger(import_name)
    deps = getattr(pkg, "SYSTEM_DEPS", None)
    pkg_on_start = getattr(pkg, "on_start", None)

    def _on_start():
        # warn (stderr) about missing system tools, then run any capability on_start
        for w in system_startup_warnings(deps):
            log.warning("system tool missing — %s", w)
        if pkg_on_start is not None:
            pkg_on_start()

    try:
        serve(
            import_name,
            pkg.__version__,
            pkg.SPECS,
            transport=getattr(pkg, "transport", None),
            on_start=_on_start,
        )
    except (BrokenPipeError, IOError) as exc:
        log.info("client disconnected (%s), shutting down", type(exc).__name__)
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("server crashed unexpectedly")
        sys.exit(1)  # surface the crash to the supervising harness with a non-zero status
