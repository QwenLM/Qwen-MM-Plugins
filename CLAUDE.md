# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Qwen-MM-Plugins is an Agent Skills + MCP Tools platform for vision-language models. Each capability lives in one directory under `src/capabilities/<name>/`, holding any of: a `skill/` (the Agent Skill) and a `<import_name>/` MCP-server package — each part optional. Main subsystems:

1. **qwen-mm-plugins-core** — Local file capability: read and visualize any file (images, video, PDF/Office, code, data, 3D, notebooks, GIS) via `read_image`/`read_video`/`media_info`/`visualize`, plus image tools (`crop`/`draw_bbox`/`save_view`). `src/capabilities/core/` (skill + `qwen_mm_plugins_core/` server).
2. **qwen-mm-plugins-api** — Cloud APIs for understanding media, split by model family into three subpackages (directory == category): `vl/` (`vision_chat`, `ocr`, `grounding` — Qwen-VL, via `shared.api_openai`), `omni/` (Qwen-Omni A/V: `omni_av_caption`, `omni_asr`/`omni_asr_timestamped`/`omni_multi_speaker_asr`, `omni_av_grounding`, `omni_av_counting`, `omni_music_caption` — via `shared.api_omni`), and `others/` (`transcribe_audio` — Qwen3-ASR, `segmentation` — SAM3); currently DashScope. 12 tools total. `src/capabilities/api/` (skill + `qwen_mm_plugins_api/` server).
3. **qwen-mm-plugins-search** — Web + reverse-image search to confirm facts: `web_search`, `web_extractor`, `image_search`; currently Serper. `src/capabilities/search/` (skill + `qwen_mm_plugins_search/` server).
4. **qwen-mm-plugins-video-memory** — Hierarchical graph memory for long video QA. 4-level tree: Root → SuperEvent → MacroEvent → Subgraph, with embedding-based semantic search. `src/capabilities/video-memory/` (skill + `qwen_mm_plugins_video_memory/` server).
5. **qwen-mm-plugins-video-edit** — Video-editing skill + image/video/audio **generation** MCP tools (DashScope, via `shared.api_dashscope`). `src/capabilities/video-edit/` (skill + `qwen_mm_plugins_video_edit/` server).
6. **qwen-mm-plugins-blender** — Blender 3D modeling: MCP tools driving a live Blender (execute Python, viewport screenshots, PolyHaven/Sketchfab/Hyper3D/Hunyuan3D assets) + a build→refine→verify skill; needs a running Blender + addon. `src/capabilities/blender/`.
7. **qwen-mm-plugins-freecad** — FreeCAD parametric CAD: MCP tools (create/edit objects, execute Python, named-view screenshots, parts library, CalculiX FEM) + a skill; needs a running FreeCAD + addon. `src/capabilities/freecad/`.
8. **qwen-mm-plugins-edu-agent** — Skill only: turns a math/science problem or image into a step-by-step Chinese explainer video or interactive page. `src/capabilities/edu-agent/`.
9. **qwen-mm-plugins-example** — Template capability (skill + server, 5 demo tools) to copy when adding your own. `src/capabilities/example/`.

## Video Content Questions — MANDATORY Skill Usage

When the user provides a video file path (`.mp4`, `.mkv`, `.avi`, `.mov`, `.webm`) or a directory of multiple videos, and asks about its content:

1. **ALWAYS invoke the `qwen-mm-plugins-video-memory` skill FIRST** — before doing anything else
2. **NEVER use ffmpeg/ffprobe to extract frames directly**
3. **NEVER answer based on a few extracted thumbnails** — a 1-hour video cannot be understood from 6 frames

The qwen-mm-plugins-video-memory skill handles: check existing memory → build if needed → query to answer.

## Common Commands

```bash
# Run MCP server (from source)
python3 src/capabilities/core/qwen_mm_plugins_core

# Install — via each harness's native plugin marketplace (reads .claude-plugin/marketplace.json; codex also .codex-plugin/)
claude plugin marketplace add <repo-url-or-path>
claude plugin install qwen-mm-plugins-core@qwen-mm-plugins

# Test MCP server
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}\n' | python3 src/capabilities/core/qwen_mm_plugins_core

# Tests / lint
python3 -m pytest tests/
ruff format . && ruff check . --fix
```

## Architecture

```
src/                                         # first-party code (shared library + all capabilities)
├── capabilities/                            # one dir per capability (skill and/or MCP server, each optional)
│   ├── core/                                # vision capability (skill + entry: qwen-mm-plugins-core)
│   │   ├── skill/                           #   Agent Skill (symlink target): SKILL.md + references/
│   │   └── qwen_mm_plugins_core/                  #   MCP server package (dir name == import name)
│   │       ├── __init__.py                  #     __version__ + SPECS (mcp_framework.build_registry) + SYSTEM_DEPS table + list_tools/on_start hooks
│   │       ├── __main__.py                  #     generic shim → mcp_framework.run_main (identical across servers)
│   │       ├── oss.py / stdio_streaming.py   (core-local utils; the shared library is src/shared/)
│   │       ├── readers/                     #     image.py (read_image), video.py (read_video)
│   │       ├── apis/                        #     analysis tools: vision_chat, ocr, grounding (OpenAI-compat via shared.api_openai), segmentation, transcribe_audio, image_search, web_*
│   │       ├── producers/                   #     crop, draw_bbox, save_view
│   │       └── renderers/ visualizers/      #     file rendering + visualize tool
│   ├── video-memory/                        # long-video graph-memory capability
│   │   ├── skill/                           #   SKILL.md + build pipeline (self-contained, flat modules)
│   │   │   └── script/build_memory/         #     build_memory.sh, build_graph.py, pipeline_worker.py, llm_client.py,
│   │   │                                    #     prompts.py, schema.py + embeddings.py (copies of the server's)
│   │   └── qwen_mm_plugins_video_memory/     #   MCP server package
│   │       ├── __init__.py                  #     __version__ + SPECS + SYSTEM_DEPS table + list_tools/on_start hooks
│   │       ├── __main__.py                  #     generic shim → mcp_framework.run_main
│   │       ├── tools/                       #     one TOOL+handle per tool: get_summary, get_super_events, get_macro_events, get_subgraph, search_nodes, enumerate_events, search_ocr_text, search_asr_text, search_by_time
│   │       ├── loader.py                    #     shared MemoryToolkit load + cache (get_toolkit) used by every tool
│   │       ├── toolkit.py                   #     MemoryToolkit: retrieval methods (drill-down pattern)
│   │       ├── schema.py                    #     data model: HierarchicalGraphMemory, VideoRoot, SuperEvent, …
│   │       ├── embeddings.py                #     EmbeddingIndex (DashScope/local, cosine similarity, NPZ)
│   │       └── query_memory.py              #     CLI: query graph memory
│   ├── video-edit/                          # video-editing skill + generation MCP server
│   │   ├── skill/                           #   SKILL.md + workflows/
│   │   └── qwen_mm_plugins_video_edit/       #   MCP server package: tools/ = qwen_image, qwen_tts, wan_s2v, wan_t2v, happyhorse (DashScope generation, via shared.api_dashscope)
│   ├── blender/                             # Blender 3D-modeling capability (skill + server): thin MCP client → a live Blender + addon (execute Python, viewport screenshot, PolyHaven/Sketchfab/Hyper3D/Hunyuan3D assets)
│   ├── freecad/                             # FreeCAD parametric-CAD capability (skill + server): thin MCP client → a live FreeCAD + FreeCADMCP addon (create/edit objects, execute Python, named-view screenshot, parts library, CalculiX FEM)
│   ├── edu-agent/                           # educational explainer-video capability (skill only): a math/science problem or image → step-by-step Chinese video or interactive page
│   └── example/                             # example/template capability (skill + server + 5 demo tools: text/image/frames + API call + env/config) — walkthrough: docs/en/how_to_add_new_capability.md
├── shared/                                  # shared LIBRARY (env/content/image/video/cache/syscmd + api_openai/api_dashscope) — reusable by every server; no __main__/tools/entry
└── mcp_framework.py                         # shared framework: build_registry + tool_schema + serve (FastMCP) + run_main + system_report/startup_warnings (every server imports it)

pyproject.toml                         # the one distribution: entries, extras (Python deps), package map, version
.claude-plugin/marketplace.json        # native plugin marketplace (per-capability manifests live in each src/capabilities/<cap>/)
plugin-versions.json                   # latest stable per-capability versions + immutable tag format
tests/  eval/  ruff.toml
```

**Naming convention**: one capability = a short folder `src/capabilities/<folder>/` (`core`, `video-memory`, `video-edit`, `blender`, `freecad`, `edu-agent`, `example`), holding any of `skill/` (the Agent Skill) and a `<import_name>/` MCP-server package (a valid Python identifier, e.g. `qwen_mm_plugins_core`). The skill, console entry, and plugin are all named `qwen-mm-plugins-<folder>` (e.g. `qwen-mm-plugins-core`) — matching the capability, so a skill's `SKILL.md` `name:` equals its install/plugin name; every published capability is listed in `.claude-plugin/marketplace.json` and `plugin-versions.json` (`example` is template-only). Tests/launch find the server package by scanning each `src/capabilities/<folder>/` for the subdir with `__init__.py`. Skill and server are each optional — skill-only, mcp-only, or both.

**Adding a capability** (full walkthrough: `docs/en/how_to_add_new_capability.md` — copy `src/capabilities/example/`): create `src/capabilities/<folder>/` with `skill/` (`SKILL.md`) and/or `<import_name>/` (server package — copy `__main__.py` verbatim from an existing server; its `__init__.py` holds the per-capability `__version__` + `SPECS, get_handler = build_registry(...)` + a `SYSTEM_DEPS` table (system tools pip can't install — the framework renders `--check-system` + startup warnings from it; each entry needs only `label`/`tools`/`hint`, with `extra`/`probe`/`startup` optional) + an optional `on_start()`; add tool modules, each exporting `TOOL` — with a Pydantic `args` model — plus `handle`). For a server, register it in `pyproject.toml` (a `[project.scripts]` entry, add its folder to `[tool.setuptools]` `package-dir` + `packages.find` `where` — subpackages are auto-discovered — and an extras group/profile); then add it to `plugin-versions.json`, add a tag-pinned `git-subdir` entry to `.claude-plugin/marketplace.json`, and write the capability's `.claude-plugin/plugin.json` (skill + inline `mcpServers`, whose server key is `qwen-mm-plugins-<cap>` — unique per capability), `.codex-plugin/plugin.json`, `.qoder-plugin/plugin.json`, and `.mcp.json` for a server. Shared code goes in a module (or package) under `src/` with no `__main__.py` (bundled + importable, no console entry) — `mcp_framework.py` (the framework) and `shared/` are exactly that. Reuse those across capabilities instead of importing a sibling capability's server package.

**Packaging and releases**: all MCP servers ship in ONE distribution — `qwen-mm-plugins`, from the hand-authored repo-root `pyproject.toml`; extras choose dependencies, not which source packages enter the wheel. Plugin releases are nevertheless independent: `plugin-versions.json` records each latest stable SemVer, marketplace `git-subdir` sources and MCP `uvx --from` specs both pin `qwen-mm-plugins-<cap>-v<version>`, and each server package reports that plugin version. `mcp_framework.__version__` is only the distribution/release-train version. A release of one cap therefore freezes the whole repository snapshot at its tag but upgrades only that plugin's independent uvx environment; shared changes require bumping every affected cap. Use `scripts/prepare_plugin_release.py`, never move a published tag, and see `docs/en/releasing.md`. System tools are declared per server in `SYSTEM_DEPS`. Native marketplace installs copy the skill and register the MCP; `install.sh` defaults every cap to its own stable tag and also automates qwen-code/gemini. OpenClaw uses an installer-managed local marketplace checkout because it rejects remote `git-subdir` entries. For local-checkout dev, first run `scripts/dev-plugin.sh <cap>` so the tag-pinned marketplace entry and MCP ref temporarily point at the checkout, then revert it after testing. **qwenpaw** remains manual. Don't install the same capability two ways.

## Key Patterns

**Tool auto-discovery** (all servers, via the shared `mcp_framework` module): create a `.py` exporting `TOOL` (a dict with `name`, `description`, and a Pydantic `args` model) + `handle(arguments) -> list[content-dict]` in a scanned subpackage and it's auto-registered at server start — no manual registration. Each package's `__init__.py` calls `mcp_framework.build_registry(__name__, [subpackages])` → `SPECS` + `get_handler()` (`list_tools()` derives the wire metadata). `run_main` → `mcp_framework.serve(...)` bridges the specs onto the SDK's **FastMCP**: it synthesizes a typed wrapper per tool (signature from the `args` model, so FastMCP generates the `inputSchema` and validates every call), overrides the advertised schema with a normalized `tool_schema(args)` (auto-`title` stripped + `$ref` inlined — kept semantically identical to the old hand-written style), then runs `handle` in a worker thread (`anyio.to_thread`); `handle` still gets a plain dict and returns `{"type": "text"|"image", ...}` blocks. core scans `readers/`/`visualizers/`/`producers/`; api scans `vl/`/`omni/`/`others/` (by model family); search and video-memory scan `tools/`, the latter resolving the shared `MemoryToolkit` via `loader.get_toolkit()`. `mcp_framework` depends only on the `mcp` SDK (which bundles FastMCP), so it doesn't couple the servers to each other.

**Graph memory build phases** (`src/capabilities/video-memory/skill/script/build_memory/build_graph.py`; `build_memory.sh` orchestrates chunked P1+P2 then P3):
- P1: `step1_scene_detect_segmentation` — HLS frame-diff scene-cut segmentation into macro events
- P2: `step2_subgraph_extraction` — per-macro subgraph (entities/events/OCR/edges); parallelized by `pipeline_worker.py`
- P3: `step3_hierarchical_aggregation` — macros → supers → root, then `EmbeddingIndex` build

When OSS creds (`OSS_AK`/`OSS_SK`) are set the VLM gets clipped-video URLs (`clip_and_upload_video`); otherwise it falls back to inline base64 frames (`extract_frames_base64`), so a build needs only `DASHSCOPE_API_KEY`.

**Video preprocessing**: Videos ≤2048×2048 pixels are precompressed to 512p 1fps H.264 (`-g 1`) for fast seek. 8K+ videos skip preprocess (AV1 decode too slow).

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `DASHSCOPE_API_KEY` | Required for vision_chat, ocr, grounding, transcribe_audio, generation, graph-memory builds |
| `DASHSCOPE_BASE_URL` | Override the DashScope OpenAI-compatible base URL |
| `SERPER_API_KEY` | Required for web_search / web_extractor / image_search |
| `SAM3_SERVER_URL` | Required for segmentation (SAM3 server URL) |
| `ASR_SERVER_URLS` | Comma-separated self-hosted ASR server URLs (transcribe_audio fallback when DashScope fails) |
| `QWEN_MM_FFMPEG_TIMEOUT` | ffmpeg timeout seconds (default: 120) |
| `QWEN_MM_CHAT_TIMEOUT` | OpenAI-compatible chat request timeout seconds (default: 600) |
| `QWEN_MM_AUDIO_RAW_B64` | Send `input_audio` as raw base64 for OpenAI-spec servers like vLLM (default: off = DashScope `data:;base64,…` form) |
| `QWEN_MM_MAX_TOTAL_FRAMES` | Max frames sampled from a video (default: 600) |
| `QWEN_MM_CACHE` | Override the cache dir for derived render artifacts (default: OS cache dir) |
| `GRAPH_MEMORY_PATH` | graph_memory.json path (video-memory MCP server; takes precedence over a passed video path) |
| `EMBEDDINGS_PATH` | embeddings.npz path (video-memory MCP server) |
| `CUTOFF_SEC` | Optional time cutoff (seconds) for video-memory retrieval |

**OSS (optional)** — only needed to serve large videos/frames by signed URL instead of inline base64.

| Variable | Scope | Purpose |
|----------|-------|---------|
| `OSS_AK` / `OSS_SK` / `OSS_ENDPOINT` | shared | Credentials + endpoint |
| `OSS_BUCKET` | build / api | Upload-destination bucket for `upload_and_sign` (memory-build clips, api video/Omni oversized media) |
| `OSS_VIDEO_CLIP_PREFIX` | build / api | Key prefix for uploaded clips (default: `tmp/video_clips`) |
| `OSS_URL_EXPIRY` | shared | Signed-URL TTL seconds (default: 7200) |

**App hosts (optional)** — blender/freecad live sessions + edu-agent rendering. Full catalog: `src/shared/env.py` `CONFIG_FIELDS` (regenerate these tables via `python3 scripts/gen_env_docs.py`).

| Variable | Scope | Purpose |
|----------|-------|---------|
| `BLENDER_BINARY` / `BLENDER_HOST` / `BLENDER_PORT` | blender | Blender executable + addon host/port (default: localhost:9876) |
| `FREECAD_BINARY` / `FREECAD_RPC_HOST` / `FREECAD_RPC_PORT` / `FREECAD_MOD_DIR` | freecad | FreeCAD executable + RPC host/port (default: localhost:9875) + Mod dir for the bundled addon |
| `NODE_PATH` / `PUPPETEER_EXECUTABLE_PATH` | edu-agent | Node.js module resolution path / headless Chromium executable for Puppeteer |
