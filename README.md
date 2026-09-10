# Qwen-MM-Plugins

**English** · [中文](README.zh.md)

Native multimodal plugins for Qwen models. Make any agent harness multimodal-native.

[Explore the Hub](https://qwenlm.github.io/qwen-mm-plugins-hub/) ·
[Installation](https://qwenlm.github.io/qwen-mm-plugins-hub/docs/) ·
[Add a plugin](https://qwenlm.github.io/qwen-mm-plugins-hub/docs/how-to-add-new-capability/)

Browse plugins by capability, preview their Skills and tool definitions, and try the cookbook
examples with embedded videos and interactive cases. The Hub also hosts the English documentation.

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
| `core` | Reads local images and video frames, and visualizes documents, code, data, 3D models and NIfTI volumes for the agent to inspect. Includes media metadata, cropping, bounding-box annotation and page/frame export. No API key in the default native mode. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/core/cookbook/) |
| `nifti` | Inspects NIfTI volumes with configurable source-axis slices, volume-level intensity normalization, explicit window presets and effective-configuration reporting. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/nifti/cookbook/) |
| `api` | Calls model services to understand images, video and audio: VL vision chat/OCR/grounding, Omni transcription/diarization/captioning/event analysis, dedicated ASR and SAM3 segmentation. Uses DashScope or compatible self-hosted services, configured per model family. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/api/cookbook/) |
| `search` | For any model. Web search and page extraction with Serper, Exa, Tavily or Serply; reverse-image search uses Serper. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/search/cookbook/) |

**Qwen VL series model** (e.g. **Qwen3.8-Max**, **Qwen3.7-Plus**):

| Capability | Use case | Cookbook |
|---|---|---|
| `video-memory` | Builds a hierarchical memory of a long video, so questions about it are answered from the memory instead of re-watching. Needs a DashScope key and ffmpeg. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/video-memory/cookbook/) |
| `video-edit` | Generates images, video and audio, and runs editing workflows over them. Needs a DashScope key, ffmpeg and Node. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/video-edit/cookbook/) |
| `blender` | Drives a running Blender: modelling, materials, lighting and rendering. Needs Blender installed. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/blender/cookbook/) |
| `freecad` | Drives a running FreeCAD: parametric CAD, STEP/STL and FEM. Needs FreeCAD installed. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/freecad/cookbook/) |
| `edu-agent` | Creates Chinese math and science explainer videos and interactive pages. Skill-only; needs Node and ffmpeg. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/edu-agent/cookbook/) |

**Qwen Omni series model** (e.g. **Qwen3.5-Omni-Plus**):

> Most harnesses cannot yet feed audio to the main model natively. For now, audio is handled through
> the API instead.

| Capability | Use case | Cookbook |
|---|---|---|
| `omni-memory` | Builds an audio-visual memory of a long video: who is present, who said what, how they said it, and what it sounded like. The Omni model reads the video together with its audio track. Needs a DashScope key and ffmpeg. | [Cookbook](https://qwenlm.github.io/qwen-mm-plugins-hub/plugins/omni-memory/cookbook/) |

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

For configurable NIfTI viewing, use the dedicated `nifti` plugin's `nifti_visualize` tool.
It defaults to three interior slices on source axis 2 with a shared volume-level P1–P99 range.
Core's basic NIfTI preview keeps its existing orthogonal-center, per-slice defaults.

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
- [Add a new plugin](docs/en/how_to_add_new_capability.md) · [Hub authoring](docs/en/hub.md) · [Testing](docs/en/testing.md)

## License

Apache-2.0 — see [LICENSE](LICENSE). Third-party attribution for the Blender and FreeCAD integrations
is recorded in their respective [Blender](src/capabilities/blender/NOTICE.md) and
[FreeCAD](src/capabilities/freecad/NOTICE.md) notices.
