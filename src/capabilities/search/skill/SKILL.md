---
name: qwen-mm-plugins-search
description: Web search and page extraction MCP tools (Serper, Exa, or Tavily) plus Serper Lens reverse-image search for confirming facts — web_search (find facts), web_extractor (read a page in depth), image_search (reverse-search a frame to identify an entity). Use to verify anything you cannot confirm from the media alone.
---

# Qwen-MM-Plugins Search

You have `qwen-mm-plugins-search` MCP tools available. With `QWEN_MM_SEARCH_BACKEND` unset or set to `auto`, `web_search` and `web_extractor` choose the first configured key in this order: `SERPER_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`. Set the selector to `serper`, `tavily`, or `exa` to pin a backend; explicit selection does not fall back when its key is missing. Independently of that selection, `image_search` always uses Serper Lens and reads `SERPER_API_KEY`.

Check the `qwen-mm-plugins-search` tools in your tool list for full schemas and parameters.

## When to Use Which Tool

- **Search the web** for facts → `web_search`
- **Read a web page** in depth → `web_extractor`
- **Reverse image search** to identify an entity from a frame/photo → `image_search`

## Confirm Before You Commit

Any question that needs external knowledge — identifying a specific thing, OR a fact about what's shown that you cannot confirm from the media alone — MUST be confirmed with a search before you answer. Never commit from appearance alone.

Typical flow (spans capabilities):
1. Watch the video with `qwen-mm-plugins-core`'s `read_video` — for a long video do a low-fps overview first (~32 frames, `fps≈32/duration`), then zoom. Don't run `ffmpeg`/montage yourself.
2. Grab the frame to search with core's `save_view` (`times=[...]`).
3. `image_search` (reverse-search the frame) and/or `web_search` to confirm the identity/fact.
4. Optionally cross-check appearance with `qwen-mm-plugins-api`'s `vision_chat`.

Details and worked examples in `references/video_search.md`.

## Relationship to Other Capabilities

- **Frames come from** `qwen-mm-plugins-core` (`save_view` / `read_video`) — this capability does not read media.
- **Model-based understanding** (caption, OCR, grounding, ASR) → `qwen-mm-plugins-api`.
