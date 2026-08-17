"""`shared` — reusable processing library for Qwen-MM-Plugins capabilities.

A plain library at the src/ root (peer to mcp_framework), importable by any
capability from source AND the wheel — it is NOT a server/capability (no __main__, no
tools, no console entry). Submodules:

  - shared.env           config/constants (TOKEN_SIZE, resolution budgets, limits) + get_env
  - shared.content       MCP content-block builders + input guards (text/image/text_error/require_*/default_output_path)
  - shared.image         PIL primitives + resolution math (draw_boxes, norm_to_pixel, save_image, budget_to_pixels, smart_resize)
  - shared.video         ffmpeg frame extraction + timestamp parsing (get_video_info, extract_frames_by_seeking, parse_time)
  - shared.cache         derived-artifact cache dir + content-keyed paths (cache_dir, cached_path)
  - shared.syscmd        locate external CLIs with PATH recovery (which_tool, find_tool)
  - shared.isolated_worker JSON-file RPC for native/crash-prone callables (run_isolated)
  - shared.api_openai    OpenAI-compatible chat client (call_openai_chat, resolve_openai_endpoint)
  - shared.api_dashscope DashScope native-REST async generation-task helpers (submit/poll/save_url)
  - shared.retry         one retry/backoff loop shared by the API clients (retry_call)
  - shared.applaunch     thin-client app launch/probe plumbing (ports, binary resolve, xvfb, autolaunch)

Reuse these across capabilities instead of importing a sibling capability's server package
(that only resolves once installed, and couples the servers together).
"""
