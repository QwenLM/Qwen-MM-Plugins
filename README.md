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

Each capability is installed independently as a **Skill** plus an optional **MCP server**, named
`qwen-mm-plugins-<capability>`. Pick by your agent's main model. We strongly recommend the `core`
plugin for multimodal models: it lets the main model read images, video and files natively, rather
than routing them through a separate API or ad-hoc shell commands.

**General**:

| Capability | Use case | Cookbook |
|---|---|---|
| `core` | For VL/Omni agentic models. Reads images and video natively, and visualizes documents, code, data, 3D files and NIfTI volumes. All on your machine, no API key. | [Cookbook](cookbooks/core/usage.md) |
| `api` | For any model, including text-only ones. Calls the multimodal model APIs with a DashScope key or local endpoint: VL `vision_chat` / `ocr` / `grounding`, Omni `omni_av_*` / `omni_asr*` / `omni_music_caption`, plus `transcribe_audio` and `segmentation`. | [Cookbook](cookbooks/api/usage.md) |
| `search` | For any model. Web search, page extraction and reverse-image search, with a Serper, Exa or Tavily key. | [Cookbook](cookbooks/search/usage.md) |
| `mhs` | For any model. Operates real hardware — cameras, sensors, lamps, arms, lab equipment — through Model Hardware Standard adapters, with host-side safety-limit enforcement and an emergency stop. Adapters are run by the hardware's owner; no cloud key. | [Cookbook](cookbooks/mhs/usage.md) |

**Qwen VL series model** (e.g. **Qwen3.8-Max**, **Qwen3.7-Plus**):

| Capability | Use case | Cookbook |
|---|---|---|
| `video-memory` | Builds a hierarchical memory of a long video, so questions about it are answered from the memory instead of re-watching. Needs a DashScope key and ffmpeg. | [Cookbook](cookbooks/video-memory/usage.md) |
| `video-edit` | Generates images, video and audio, and runs editing workflows over them. Needs a DashScope key, ffmpeg and Node. | [Cookbook](cookbooks/video-edit/usage.md) |
| `blender` | Drives a running Blender: modelling, materials, lighting and rendering. Needs Blender installed. | [Cookbook](cookbooks/blender/usage.md) |
| `freecad` | Drives a running FreeCAD: parametric CAD, STEP/STL and FEM. Needs FreeCAD installed. | [Cookbook](cookbooks/freecad/usage.md) |
| `edu-agent` | Creates Chinese math and science explainer videos and interactive pages. Skill-only; needs Node and ffmpeg. | [Cookbook](cookbooks/edu-agent/usage.md) |

**Qwen Omni series model** (e.g. **Qwen3.5-Omni-Plus**):

> Most harnesses cannot yet feed audio to the main model natively. For now, audio is handled through
> the API instead.

| Capability | Use case | Cookbook |
|---|---|---|
| `omni-memory` | Builds an audio-visual memory of a long video: who is present, who said what, how they said it, and what it sounded like. The Omni model reads the video together with its audio track. Needs a DashScope key and ffmpeg. | [Cookbook](cookbooks/omni-memory/usage.md) |

Exact versions and optional extras are in the
[installation guide](docs/en/installation.md#dependencies).

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
