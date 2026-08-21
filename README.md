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
| `core` | Read images and video; visualize documents, code, data, 3D files, NIfTI volumes, and more | No API key; ffmpeg for audio/video; nibabel for NIfTI; format-specific apps as needed | [Cookbook](cookbooks/core/usage.md) |
| `api` | Qwen VL/Omni vision, OCR, grounding, ASR, segmentation, and audio-video understanding | DashScope; ffmpeg for local audio/video | [Cookbook](cookbooks/api/usage.md) |
| `search` | Web search, page extraction, and reverse-image search | Serper, Exa, or Tavily key; image search requires Serper | [Cookbook](cookbooks/search/usage.md) |
| `video-memory` | Build hierarchical memory for long-video QA | DashScope; ffmpeg/ffprobe for builds | [Cookbook](cookbooks/video-memory/usage.md) |
| `video-edit` | Image, video, and audio generation with editing workflows | DashScope; ffmpeg + Node/Chromium for full edits | [Cookbook](cookbooks/video-edit/usage.md) |
| `blender` | Model, texture, light, and render in Blender | Blender; Xvfb on headless Linux | [Cookbook](cookbooks/blender/usage.md) |
| `freecad` | Parametric CAD, STEP/STL, and FEM workflows | FreeCAD; CalculiX for FEM; Xvfb on headless Linux | [Cookbook](cookbooks/freecad/usage.md) |
| `edu-agent` | Create Chinese math/science explainer videos and interactive pages | Skill-only; Node/Chromium + ffmpeg; DashScope for narrated video | [Cookbook](cookbooks/edu-agent/usage.md) |

## Try it

After installing a capability, reference a file and ask naturally; the Skill selects the relevant
MCP tool.

```text
@report.pdf          Summarize page 3 and extract its table.
@meeting.mp4         Transcribe this with speaker labels and timestamps.
@place.jpg           Identify where this photo was taken and verify it on the web.
@lecture-2h.mp4      List the main points with timestamps.
@brain.nii.gz        Inspect metadata and show orthogonal center slices.
```

`core` reads media at dynamic resolution, so manual resizing is normally unnecessary.
NIfTI files stay local and are opened read-only; this visualization is not for clinical diagnosis.

## Requirements and configuration

- [`uv`](https://docs.astral.sh/uv/) provides `uvx`, which installs Python dependencies on demand.
- Local `core` tools need no API key in the default native-image mode. Text-only caption fallback,
  cloud, and search capabilities need their provider credentials.
- Video, document, browser, Blender, and FreeCAD workflows may need system applications.

Run the installer's **Configure** and **Verify** actions to set credentials and check dependencies.
See [Installation](docs/en/installation.md#dependencies) for prerequisites and the
[configuration reference](docs/en/configuration.md) for every setting.

## Documentation

- [Installation](docs/en/installation.md)
- [Configuration](docs/en/configuration.md)
- [Contributing](CONTRIBUTING.md) · [Local development](docs/en/local_development.md)
- [Add a capability](docs/en/how_to_add_new_capability.md) · [Testing](docs/en/testing.md)

## License

Apache-2.0 — see [LICENSE](LICENSE). Third-party attribution for the Blender and FreeCAD integrations
is recorded in their respective [Blender](src/capabilities/blender/NOTICE.md) and
[FreeCAD](src/capabilities/freecad/NOTICE.md) notices.
