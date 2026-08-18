# Qwen-MM-Plugins

**English** · [中文](README.zh.md)

Native multimodal plugins for Qwen models. Make any agent harness multimodal-native.

## Architecture

![Qwen-MM-Plugins architecture](docs/assets/architecture.svg)

## Install

The guided installer supports Claude Code, CodeBuddy, Codex, Qoder, OpenClaw, Qwen Code, and Gemini
CLI. Shared configuration lives in `~/.qwen-mm-plugins/config`.

In-app setup for WorkBuddy, QoderWork, and QwenWork, plus manual setup for DeepSeek Harness, Hermes
Agent, opencode, pi, and QwenPaw, is documented in the
[other harness guide](docs/en/manual_harnesses.md).

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash
```

Update the capabilities already installed in one harness:

```bash
curl -fsSL https://raw.githubusercontent.com/QwenLM/Qwen-MM-Plugins/main/install.sh | bash -s -- update
```

Released capabilities use independent, immutable tags. For local checkout installs, rollback,
manual skill + MCP setup, dependencies, and Windows/WSL2, see the
[installation guide](docs/en/installation.md).

## Capabilities

Each capability is installed independently as a **Skill** plus an optional **MCP server**. Its
install name is `qwen-mm-plugins-<capability>`.

| Capability | Use case | Main requirements | Cookbook |
|---|---|---|---|
| `core` | Read images and video; visualize documents, code, data, 3D files, and more | No API key; ffmpeg for audio/video; format-specific apps as needed | [Cookbook](cookbooks/core/usage.md) |
| `api` | Qwen VL/Omni vision, OCR, grounding, ASR, segmentation, and audio-video understanding | DashScope; ffmpeg for local audio/video | [Cookbook](cookbooks/api/usage.md) |
| `search` | Web search, page extraction, and reverse-image search | Serper, Exa, or Tavily key; image search requires Serper | [Cookbook](cookbooks/search/usage.md) |
| `video-memory` | Build hierarchical memory for long-video QA | DashScope; ffmpeg/ffprobe for builds | [Cookbook](cookbooks/video-memory/usage.md) |
| `video-edit` | Image, video, and audio generation with editing workflows | DashScope; ffmpeg + Node/Chromium for full edits | [Cookbook](cookbooks/video-edit/usage.md) |
| `blender` | Model, texture, light, and render in Blender | Blender; Xvfb on headless Linux | [Cookbook](cookbooks/blender/usage.md) |
| `freecad` | Parametric CAD, STEP/STL, and FEM workflows | FreeCAD; CalculiX for FEM; Xvfb on headless Linux | [Cookbook](cookbooks/freecad/usage.md) |
| `edu-agent` | Create Chinese math/science explainer videos and interactive pages | Skill-only; Node/Chromium + ffmpeg; DashScope for narrated video | [Cookbook](cookbooks/edu-agent/usage.md) |
| `proxy` | Local protocol proxy that gives text-only models vision (intercepts images, transcribes via a VLM, forwards text) | VLM key + an upstream text-model endpoint | [vision-proxy](#vision-proxy-give-text-only-models-eyes-quick-start) |

> Note: `proxy` is shipped as a **local HTTP proxy** (a resident service), not as a Skill + MCP server;
> see the dedicated [vision-proxy](#vision-proxy-give-text-only-models-eyes-quick-start) section below.

## Try it

After installing a capability, reference a file and ask naturally; the Skill selects the relevant
MCP tool.

```text
@report.pdf          Summarize page 3 and extract its table.
@meeting.mp4         Transcribe this with speaker labels and timestamps.
@place.jpg           Identify where this photo was taken and verify it on the web.
@lecture-2h.mp4      List the main points with timestamps.
```

`core` reads media at dynamic resolution, so manual resizing is normally unnecessary.

## Requirements and configuration

- [`uv`](https://docs.astral.sh/uv/) provides `uvx`, which installs Python dependencies on demand.
- Local `core` tools need no API key. Cloud and search capabilities need their provider credentials.
- Video, document, browser, Blender, and FreeCAD workflows may need system applications.

Run the installer's **Configure** and **Verify** actions to set credentials and check dependencies.
See [Installation](docs/en/installation.md#dependencies) for prerequisites and the
[configuration reference](docs/en/configuration.md) for every setting.

## Documentation

- [Installation](docs/en/installation.md)
- [Configuration](docs/en/configuration.md)
- [Contributing](CONTRIBUTING.md) · [Local development](docs/en/local_development.md)
- [Add a capability](docs/en/how_to_add_new_capability.md) · [Testing](docs/en/testing.md)

## vision-proxy: give text-only models eyes (quick start)

**What it does**: runs a local service (default `127.0.0.1:8787`). Claude Code / Codex /
Qwen Code point their base_url at it; it intercepts images, transcribes them via a vision
model (VLM, e.g. mimo-v2.5), and forwards the *text* to the upstream text model. The upstream
only ever sees text, so a text-only model can "read" images.

**What the proxy supports**
- **Inbound node types**: `responses`, `chat`, and Anthropic (`/v1/messages`). Detection is
  fully automatic - there is no config for it. The proxy matches the request **path** first
  (`/v1/messages` -> Anthropic, `/v1/responses` -> Responses, `/v1/chat/completions` -> Chat)
  and falls back to the **body structure** when the path is not recognized (an `input` field ->
  Responses, a `messages` list -> Anthropic, otherwise Chat). A request that matches neither is
  rejected with a 400.
- **Model types**: both **vision-capable VLM models** (images pass through untouched) and
  **text-only models** (images are transcribed into a description) are supported. The
  `model_capabilities` map in the config decides which is which.
- **How the upstream model is identified**: the model-name -> vision/text mapping lives in the
  proxy config. On every `start` the proxy scans the harness config files (Claude Code / Codex /
  Qwen Code), groups the discovered models by harness, and interactively asks you to confirm
  only models it has not seen before (default: text-only, the safe choice). Already-confirmed
  models are reused silently; run `qwen-mm-plugins-proxy models` to review or edit the map.

**You need to prepare**
1. an API key for a vision-capable VLM (mimo / qwen-vl / Doubao / ...), used in `vlm.api_key`;
2. the upstream endpoints for **both** the text model (`relays[].base_url`) and the VLM
   (`vlm.base_url`): either side can be an OpenAI-compatible (chat) or Anthropic-format endpoint,
   and the text side additionally supports the Responses format. Which one is in use is set by
   `relays[].protocol` / `vlm.format`; Volcengine / DeepSeek etc. all work as long as the
   endpoint speaks one of those formats.

**Three steps**:
1. Edit `~/.qwen-mm-plugins/proxy.json` (create it if missing) with the template below (put in your own keys):

```json
{
  "server": { "bind_port": 8787 },
  "relays": [
    { "name": "my-text", "protocol": "chat",
      "base_url": "https://<your-upstream>", "api_key": "<YOUR_UPSTREAM_KEY>", "models": ["*"] }
  ],
  "vlm": {
    "model": "mimo-v2.5",
    "base_url": "https://<your-vlm-endpoint>", "api_key": "<YOUR_VLM_KEY>", "format": "chat"
  },
  "model_capabilities": { "global": { "minimax-m3": "vision", "doubao-seed-2.1-turbo": "vision" } }
}
```

2. Start: `qwen-mm-plugins-proxy start` (the first run interactively asks you to confirm which
   models support images; afterwards start/stop auto-wire and restore without prompting).
3. Verify: paste an image in Claude Code / Codex / Qwen Code and ask "what is this", then
   `qwen-mm-plugins-proxy logs` shows `injected:1` on success.

**Config rewrites happen only on `start` / `stop`**: the proxy backs up and rewrites the
three harness base_urls when it starts, and restores them when it stops. While it is running it
never watches or rewrites any config file - if you edit a harness config (switch models, change
relays) or `proxy.json` while the proxy is up, it will not react and will not repair anything;
your changes take effect on the next `start` (or a restart).

**Commands**: `start` / `stop` / `status` / `logs` / `check` / `models` (edit model capability) /
`models-scan` / `test-image`.

> For the full step-by-step guide (three-layer topology, coexisting with CC Switch / Codex++,
> troubleshooting) see `docs/superpowers/plans/2026-08-16-proxy-phase1-manual-test.md`.


## License

Apache-2.0 — see [LICENSE](LICENSE). Third-party attribution for the Blender and FreeCAD integrations
is recorded in their respective [Blender](src/capabilities/blender/NOTICE.md) and
[FreeCAD](src/capabilities/freecad/NOTICE.md) notices.
