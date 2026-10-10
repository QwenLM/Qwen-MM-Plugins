# Configuration

For most setups, open **Configure** in `install.sh`. It stores shared settings in
`~/.qwen-mm-plugins/config` as `KEY=VALUE` lines with mode `600`. Qwen-MM-Plugins components launched
by any harness read that file. A process environment variable overrides the same key in the file.

Call-time settings pick up edits to this file automatically, including atomic replacements,
creation, and deletion. Each lookup checks the file's path and metadata; unchanged files reuse
the parsed cache. Programmatic callers can still use `get_env(..., refresh_config=True)` to force
a re-read when a filesystem does not report changed metadata. Use an atomic save (as the Configure
command does) to keep concurrent readers from seeing a partially written file.

Media helpers using `QWEN_MM_FFMPEG_TIMEOUT` read it when each ffmpeg/ffprobe subprocess starts;
running subprocesses keep their existing timeout and per-operation minimum budgets still apply.
Each `read_video` call takes one snapshot of
`QWEN_MM_MAX_TOTAL_FRAMES`: omitting `max_frames` uses that current limit, and an explicit value is
capped by it. Raising or lowering the limit affects the next call, including increases above 600.
MHS bearer tokens, `QWEN_MM_MHS_DEVICES`, and `QWEN_MM_MHS_CACHE_TTL` also pick up config-file edits
on subsequent lookups; MHS device metadata retains its separate cache (60 seconds by default).

Changes to a launcher's environment require reconnecting its MCP server: an existing process
cannot inherit later shell exports. Settings captured by a running operation or an established
application connection remain in effect until that operation or connection ends.

Use `QWEN_MM_CONFIG=/path/to/file` to select another file, or `QWEN_MM_CONFIG_DIR=/path/to/dir` to
change the directory containing the default `config` file. These bootstrap variables must be set
in the process environment because they determine which file is read.

Run `bash install.sh configure` for the interactive menu, or pass one or more catalog fields for
non-interactive configuration:

```bash
bash install.sh configure DASHSCOPE_API_KEY="$DASHSCOPE_API_KEY" QWEN_MM_NATIVE_MODE=1
bash install.sh configure 'QWEN_MM_CACHE=/path/with spaces/cache'
bash install.sh configure QWEN_MM_CACHE=  # remove the override and restore the default
```

All arguments are validated before writing. Values must be single-line strings; spaces and `=`
are supported. The command preserves other settings, keeps permissions at `600`, and prints only
key names, never values. Environment variables continue to take precedence.

Installed MCP entry points also support `--set KEY=VALUE` and `--unset KEY`, for example
`qwen-mm-plugins-blender --set BLENDER_PORT=9876`. `--set` rejects empty or whitespace-only values
with exit status 2 before writing any entries. To remove an override and restore its default, use
`qwen-mm-plugins-blender --unset BLENDER_PORT`.

## Invalid runtime configuration

Unset or blank settings use their defaults. Surrounding whitespace is stripped. A blank environment
variable still overrides the file: it selects the default, not the file's value. Ordinary defaults
and validation constraints are defined once in the Pydantic-based `CONFIG_FIELDS` catalog and shared
by installed plugins and standalone skill scripts. Configure and this document use the same definitions.
Explicit invalid overrides for timeouts, frame limits, ports, output mode, search selection,
video-memory's cutoff, MHS cache TTL, and Omni Memory numeric/boolean tuning return a clear error message
when the affected operation is called. The error identifies the setting and accepted values;
it does not echo the configured value. Startup and tool discovery remain available, so a harness
can receive the error instead of losing its connection. Correct the config file and retry the call.
If an environment variable overrides the file, correct that variable in the launcher and reconnect
the MCP server before retrying.

- `QWEN_MM_FFMPEG_TIMEOUT`, `QWEN_MM_CHAT_TIMEOUT`, `QWEN_MM_MAX_TOTAL_FRAMES`, and
  `OSS_URL_EXPIRY` must be positive integers.
- `BLENDER_PORT` and `FREECAD_RPC_PORT` must be integers from 1 through 65535.
- `CUTOFF_SEC` must be finite, non-negative seconds, such as `90` or `90.5`. Unset or blank
  means no cutoff; invalid values do not silently disable the cutoff.
- Boolean switches accept `1/0`, `true/false`, `yes/no`, and `on/off`, ignoring case and
  surrounding whitespace.
- `QWEN_MM_MHS_CACHE_TTL` must be finite non-negative seconds; `0` disables metadata caching.
- A missing config file means no overrides. An unreadable/non-UTF-8 file, malformed `KEY=VALUE`
  line, or unclosed quoted value is an error. Syntax errors identify the line without echoing its value.

Service logs still go to stderr for diagnostics. Configuration failures are also returned in tool
responses; seeing them does not require access to the harness's server logs. Existing handlers may
return error text, while uncaught exceptions use the MCP SDK's normal error response. The plugin does
not force an `isError` flag on text responses. Standalone scripts
raise the same configuration exceptions. Both paths require `pydantic>=2.11,<3`; standalone scripts
need it in their own interpreter, even when the MCP server already has it installed. There is no
warning-and-default or permissive-reader mode.

## Configuration migration

When updating existing configurations:

- Fix invalid overrides instead of relying on warnings, clamping, or silent fallback. Integers must
  be plain integers: for example, use `120` for a timeout, not `120s`, `1.5`, or `15 MiB`.
- `QWEN_MM_CHAT_TIMEOUT` now defaults to **1800 seconds in every consumer**. This preserves the
  previous Omni and video-memory builder timeout; the general chat default was 600 and
  `read_native_av` used 900. Set it explicitly to choose a different timeout.
  Explicit tool/request arguments still take precedence over configuration.
- Blank strings now select defaults consistently. To disable a boolean setting, use `0` or `false`.
- Config-file edits are detected automatically, including creation, deletion and atomic saves.
  Correct a bad file value and retry in the same MCP session. Changing the launcher's environment
  still requires restarting/reconnecting the server.
- `read_video` resolves an omitted `max_frames` at call time. Its advertised schema no longer fixes
  that argument to 600. Both configuration and explicit frame limits can be as small as 1.
- Video-memory accepts `HH:MM:SS` for ordinary videos and `dayN_HH:MM:SS` / `dayN HH:MM:SS` for
  EgoLife. Provided string bounds override numeric bounds. Invalid times and reversed intervals
  are errors instead of being ignored; invalid `CUTOFF_SEC` does not disable the cutoff.

The ordinary catalog covers shared settings. Capability-private fields keep their defaults in the
owning capability and use the same readers. Omni Memory's `MEM_*` numeric/boolean settings now
honor file-only configuration and are read when used, rather than during module import.
Video-memory's builder also reads its VL model, endpoint, timeout and OSS credentials at use time;
embedding requests resolve the current endpoint when called.

## Model output mode

`QWEN_MM_NATIVE_MODE=1` is the default. MCP tools return native image content blocks so a
multimodal host model can inspect the original visual result directly.

Set `QWEN_MM_NATIVE_MODE=0` when the host model is text-only. Every returned image block is replaced
at the same position by a generated caption, while existing text blocks (file metadata, PDF text
layers, video timestamps, and similar context) are preserved. The caption path uses
`DASHSCOPE_BASE_URL` and `QWEN_MM_API_VL_MODEL`. Credentials are selected by endpoint: DashScope uses
`DASHSCOPE_API_KEY`, OrcaRouter uses `ORCAROUTER_API_KEY`, OpenRouter uses `OPENROUTER_API_KEY`, and
Cheaper Inference uses `CHEAPER_INFERENCE_API_KEY`.
Any other OpenAI-compatible endpoint set as `DASHSCOPE_BASE_URL` uses `DASHSCOPE_API_KEY`, which is
sent only to that URL's origin (scheme, host, port).
Authentication-free local endpoints need no key configuration. A failed caption call produces an explicit
`Visual content unavailable` text block instead of exposing base64 or silently dropping the image.

Enabling text-only mode sends tool-result images—including local files and application or desktop
screenshots—to the configured VL endpoint. Use it only when that data may be shared with the
endpoint. Invalid mode values return an MCP tool error before the tool runs and do not trigger an upload.

## Search selection

Leave `QWEN_MM_SEARCH_BACKEND` unset or set it to `auto` to select the first configured key in this
fixed order: Serper, Tavily, Exa, Serply. Set it to `serper`, `tavily`, `exa`, or `serply` to pin one
provider; a
missing matching key then raises an error instead of falling back. `image_search` always uses
Serper Lens and therefore always requires `SERPER_API_KEY`.

## Configure catalog

This is the complete catalog shown by the installer's **Configure** action. Defaults and grouping
come from [`CONFIG_FIELDS`](../../src/shared/env.py); `—` means unset or disabled.

<!-- BEGIN GENERATED CONFIG CATALOG -->
<!-- Generated by scripts/gen_env_docs.py from src/shared/env.py CONFIG_FIELDS. -->

### Media APIs & endpoints

| Variable | Default | Purpose |
|---|---|---|
| `DASHSCOPE_API_KEY` | — | vision, OCR, grounding, text-only image captions, ASR, generation, memory builds *(secret)* |
| `ORCAROUTER_API_KEY` | — | OpenAI-compatible calls to api.orcarouter.ai *(secret)* |
| `OPENROUTER_API_KEY` | — | OpenAI-compatible calls to openrouter.ai *(secret)* |
| `CHEAPER_INFERENCE_API_KEY` | — | OpenAI-compatible calls to api.cheaperinference.com *(secret)* |
| `MINIMAX_API_KEY` | — | MiniMax text-to-speech generation *(secret)* |
| `DASHSCOPE_BASE_URL` | https://dashscope.aliyuncs.com/compatible-mode/v1 | override the DashScope OpenAI-compatible base URL |
| `DASHSCOPE_UPLOAD_POLICY_URL` | — | temporary OSS policy endpoint for oversized Omni and VL media; inferred for official DashScope hosts |
| `QWEN_MM_API_VL_MODEL` | qwen3.7-plus | default VL model for vision_chat, OCR, grounding, text-only image captions, and video-spatio VLM tools |
| `QWEN_MM_API_OMNI_MODEL` | qwen3.8-omni-flash | default Omni model for audio/video understanding tools, omni-memory, and Omni ChatCut |
| `SAM3_SERVER_URL` | — | segmentation SAM3 server URL |
| `ASR_SERVER_URLS` | — | self-hosted ASR fallback URLs (comma-separated) |

### Omni ChatCut

| Variable | Default | Purpose |
|---|---|---|
| `QWEN_MM_OMNI_CHATCUT_MODEL_CONFIG` | — | path to the shared Omni, image-provider, and video-provider connection JSON |
| `QWEN_MM_DUBBING_SERVER_URL` | — | external IndexTTS2/Demucs/TEN-VAD service used by video translation |

### Search providers

| Variable | Default | Purpose |
|---|---|---|
| `QWEN_MM_SEARCH_BACKEND` | auto | text search backend (auto: serper > tavily > exa > serply; or choose one) |
| `SERPER_API_KEY` | — | Serper web_search / web_extractor and Serper-only image_search *(secret)* |
| `TAVILY_API_KEY` | — | Tavily web_search / web_extractor *(secret)* |
| `EXA_API_KEY` | — | Exa web_search / web_extractor *(secret)* |
| `SERPLY_API_KEY` | — | Serply web_search / web_extractor *(secret)* |

### Runtime paths & limits

| Variable | Default | Purpose |
|---|---|---|
| `QWEN_MM_CACHE` | — | cache dir for derived render artifacts; defaults to the OS cache dir |
| `QWEN_MM_FFMPEG_TIMEOUT` | 120 | ffmpeg/ffprobe timeout seconds |
| `QWEN_MM_CHAT_TIMEOUT` | 1800 | OpenAI-compatible chat request timeout seconds |
| `QWEN_MM_NATIVE_MODE` | 1 | 1 returns MCP images; 0 sends images to the VL endpoint and returns captions |
| `QWEN_MM_MAX_TOTAL_FRAMES` | 600 | max frames sampled from a video |

### OSS storage (serve large media by URL)

| Variable | Default | Purpose |
|---|---|---|
| `OSS_AK` | — | OSS access key id *(secret)* |
| `OSS_SK` | — | OSS access key secret *(secret)* |
| `OSS_ENDPOINT` | — | OSS endpoint |
| `OSS_BUCKET` | — | upload destination for build clips and oversized API media |
| `OSS_VIDEO_CLIP_PREFIX` | tmp/video_clips | key prefix for uploaded video clips |
| `OSS_URL_EXPIRY` | 7200 | signed-URL TTL seconds |

### Video-memory

| Variable | Default | Purpose |
|---|---|---|
| `GRAPH_MEMORY_PATH` | — | graph_memory.json path (overrides a passed video path) |
| `EMBEDDINGS_PATH` | — | embeddings.npz path |
| `CUTOFF_SEC` | — | time cutoff (seconds) for retrieval |

### Omni-memory

| Variable | Default | Purpose |
|---|---|---|
| `MEM_LOCAL_DIR` | — | optional shared root for namespace memories; defaults beside the input video |

### Blender / FreeCAD hosts

| Variable | Default | Purpose |
|---|---|---|
| `BLENDER_BINARY` | — | path to the Blender executable |
| `BLENDER_HOST` | localhost | Blender addon host |
| `BLENDER_PORT` | 9876 | Blender addon port |
| `FREECAD_BINARY` | — | path to the FreeCAD executable |
| `FREECAD_RPC_HOST` | localhost | FreeCAD RPC host |
| `FREECAD_RPC_PORT` | 9875 | FreeCAD RPC port |
| `FREECAD_MOD_DIR` | — | FreeCAD Mod dir for the bundled addon |

### edu-agent (Node / headless Chromium)

| Variable | Default | Purpose |
|---|---|---|
| `NODE_PATH` | — | Node.js module resolution path |
| `PUPPETEER_EXECUTABLE_PATH` | — | headless Chromium executable for Puppeteer |

<!-- END GENERATED CONFIG CATALOG -->

## Advanced environment-only switches

These switches are intentionally not written by **Configure**. Set them only for the named
compatibility or runtime behavior.

| Variable | Default | Purpose |
|---|---|---|
| `QWEN_MM_AUDIO_RAW_B64` | off | Send Omni audio as raw base64 for OpenAI-spec servers such as vLLM; DashScope uses `data:;base64,<payload>` |
| `QWEN_MM_AUTOLAUNCH` | off | Launch a local Blender/FreeCAD application on the first tool call; plugin manifests may preset it |
| `QWEN_MM_NO_AUTO_INSTALL` | off | Disable automatic Blender/FreeCAD binary downloads |
| `FREECAD_ONLY_TEXT_FEEDBACK` | off | Omit screenshots normally attached to FreeCAD tool results |
| `OSS_BUCKET_NAME` | — | Legacy video-memory alias; `OSS_BUCKET` takes precedence |

Capability-specific context and prerequisites remain in each capability's Skill and cookbook.
