# Cookbook — Qwen-MM-Plugins API

`qwen-mm-plugins-api` calls cloud models to understand images, video, and audio. Local file reading
and visualization live in [`core`](../core/usage.md); web verification lives in
[`search`](../search/usage.md).

---

## Tools

### VL — Qwen-VL through an OpenAI-compatible endpoint

- `vision_chat` — chat about one or more images or videos; accepts local paths and URLs through its
  `images` and `videos` lists and supports `dry_run=true`
- `ocr` — recognize text in a local image
- `grounding` — locate objects in a local image; returns both pixel boxes and normalized `0–1000`
  boxes and can optionally return an annotated preview

Pass `grounding`'s `bbox_normalized` values—not `bbox_pixel`—to core's `draw_bbox`.

### Omni — Qwen-Omni audio/video understanding

| Tool | Use it for | Main output |
|---|---|---|
| `omni_asr` | Plain speech transcription | Continuous transcript |
| `omni_asr_timestamped` | Sentence- or word-level ASR | Timestamped JSON and optional SRT |
| `omni_multi_speaker_asr` | Speaker diarization | Speaker-labelled segments and optional SRT |
| `omni_av_caption` | Detailed audio/video review | Five-section Markdown report: storyline, visible text, speaker transcript, compliance alerts, and safety findings |
| `omni_av_grounding` | Find when an event appears | Matching start/end times |
| `omni_av_counting` | Count an event, object, or action | Count plus occurrence timestamps |
| `omni_music_caption` | Analyze a complete music track | Structured music tags and an English caption |

All Omni tools accept a local audio/video `file_path` or an HTTP(S)/OSS URL and support
`dry_run=true`. The audio/video tools also expose `fps` and `max_pixels` where visual sampling is
relevant.

### Other backends

- `transcribe_audio` — transcribe a local audio/video file with Qwen3-ASR (default
  `qwen3-asr-flash`) or `ASR_SERVER_URLS`; outputs SRT, text, or JSON
- `segmentation` — text-prompted segmentation of a local image through a self-hosted SAM3 server

For exact schemas, check the installed Skill or MCP tool list; these groups intentionally do not
share one universal input schema.

---

## Runtime tool and model selection

The agent can choose a tool and override its backend model for each call. State both explicitly in
the prompt when the distinction matters, for example:

```text
Use vision_chat with model qwen3.6-flash to summarize the slides in @demo.mp4, then use
omni_asr_timestamped with model qwen3.5-omni-plus to produce sentence-level subtitles.
```

This selects the model called by `qwen-mm-plugins-api`; it does not change the host agent's own
model. VL calls resolve the model as explicit `model` → `QWEN_MM_API_VL_MODEL` → `qwen3.7-plus`.
Omni calls use explicit `model` → `QWEN_MM_API_OMNI_MODEL` → `qwen3.5-omni-plus`. One prompt may
therefore mix tools and models without changing the configured defaults.

MCP `tools/list` shows the available tools and their schemas, but the plugin does not expose a
dynamic `list_models` tool. The following model IDs are practical examples, not an exhaustive or
per-account availability guarantee. Check the linked provider catalogs because region, workspace,
activation, and model lifecycle can differ.

### `vision_chat` model examples

| Model ID | Suggested use | Notes |
|---|---|---|
| `qwen3.7-plus` | Flagship image/video understanding | Built-in default; up to two-hour videos on supported DashScope regions |
| `qwen3.6-plus` | Strong image/video understanding | Alternative Qwen general-purpose visual model |
| `qwen3.6-flash` | Lower-cost, lower-latency image/video understanding | Recommended cost-oriented alternative |
| `qwen3-vl-plus` | Qwen3-VL visual reasoning | Older dedicated VL family; up to one-hour videos |
| `qwen3-vl-flash` | Faster Qwen3-VL visual reasoning | Older dedicated VL family; up to one-hour videos |
| `kimi/kimi-k3` | Third-party image/video understanding | Beijing workspace endpoint; requires the corresponding product activation |

See Model Studio's [visual-understanding catalog](https://help.aliyun.com/en/model-studio/vision-model/)
and [Kimi API guide](https://help.aliyun.com/en/model-studio/kimi-api) for current IDs, snapshots,
regional endpoints, and limits. A self-hosted OpenAI-compatible endpoint may accept other model IDs.

### Omni model examples

| Model ID | Suggested use | Notes |
|---|---|---|
| `qwen3.5-omni-plus` | Highest-quality audio/video understanding | Built-in default; non-realtime HTTP alias |
| `qwen3.5-omni-flash` | Lower-cost audio/video understanding | Non-realtime HTTP alias |
| `qwen3-omni-flash` | Short, cost-sensitive audio/video requests | Non-realtime HTTP; input limited to about 150 seconds |
| `qwen3.5-omni-plus-2026-03-15` | Reproducible Plus behavior | Snapshot behind the current Plus alias at publication time |
| `qwen3.5-omni-flash-2026-03-15` | Reproducible Flash behavior | Snapshot behind the current Flash alias at publication time |

See Model Studio's [Omni catalog](https://help.aliyun.com/en/model-studio/omni/) for current model
IDs and limits. Do not pass a `*-realtime` model to these tools: realtime models use a WebSocket
API, while this plugin uses non-realtime HTTP chat completions.

---

## Install

```bash
claude plugin marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
claude plugin install qwen-mm-plugins-core@qwen-mm-plugins  # local reading/annotation
claude plugin install qwen-mm-plugins-api@qwen-mm-plugins
```

`core` is not a Python dependency of `api`, but it supplies the local reading, frame extraction, and
annotation steps commonly used around API calls.

---

## Requirements and configuration

| Requirement | Used by |
|---|---|
| `DASHSCOPE_API_KEY` | VL, Omni, and the default Qwen3-ASR path |
| `DASHSCOPE_BASE_URL` | VL and Omni OpenAI-compatible calls; it does not redirect native Qwen3-ASR |
| `QWEN_MM_API_VL_MODEL` | Default model for `vision_chat`, `ocr`, and `grounding` when a call omits `model` |
| `QWEN_MM_API_OMNI_MODEL` | Default model for all Omni tools when a call omits `model` |
| `QWEN_MM_AUDIO_RAW_B64=1` | Self-hosted OpenAI-spec Omni servers that expect raw audio base64; leave unset for DashScope |
| `ASR_SERVER_URLS` | Optional self-hosted Qwen3-ASR fallback; can be used without a DashScope key |
| `SAM3_SERVER_URL` | Required only for `segmentation` |
| ffmpeg + ffprobe | Local video sampling, audio extraction, fitting, and transcoding |

Set configuration through the installer's **Configure** action, environment variables, or
`~/.qwen-mm-plugins/config`; environment variables take precedence. `bash install.sh verify` checks
system dependencies and reports the DashScope key, but it does not make live requests to every
configured provider.

Pointing `DASHSCOPE_BASE_URL` at a server other than DashScope is supported. When optional
DashScope-only request hints are present, a 400/422 response drops those hints and retries the call
once without them. This applies to `grounding`'s `enable_thinking` optimization and `vision_chat`'s
opt-in `vl_high_resolution_images`; the latter falls back to the endpoint's default resolution.

### Optional OSS delivery

OSS requires all of `OSS_AK`, `OSS_SK`, `OSS_ENDPOINT`, and `OSS_BUCKET`, plus the Python `oss2`
dependency. The standard marketplace command above installs `[api]`, not `[api,oss]`; to use the OSS
path, register an MCP command with both extras against the same released tag:

```bash
claude mcp add qwen-mm-plugins-api-oss -- \
  uvx --from \
  "qwen-mm-plugins[api,oss] @ git+https://github.com/QwenLM/Qwen-MM-Plugins.git@qwen-mm-plugins-api-v<version>" \
  qwen-mm-plugins-api
```

Do not keep this direct registration enabled alongside the marketplace API MCP server.

---

## Video delivery

Remote HTTP(S)/OSS URLs are passed to the model for server-side fetching. Local videos follow two
different routes:

- **VL (`vision_chat`)** — with complete OSS configuration and the `oss` extra, a video within the
  model's duration cap is uploaded and passed as a signed URL. Otherwise it is sampled into local
  inline frames, capped at 250 total media items.
- **Omni** — first transcodes the video to fit one inline media item. If it cannot fit, it uses OSS
  when available; otherwise it falls back to sampled frames plus a fitted audio track. A video over
  the model's server-side duration cap goes directly to the frames + audio route. Extremely long
  audio can still exceed the inline budget, so this fallback is not an unlimited transport.

`dry_run=true` previews routing without uploading or calling the model.

For whole-video QA over long recordings, use [`video-memory`](../video-memory/usage.md) to locate
candidate segments, then inspect a narrow interval with core's `read_video`.

---

## Example requests

```text
@receipt.jpg
OCR this receipt and total the line items.

@meeting.mp4
Transcribe this meeting with speaker labels and sentence-level timestamps. Return SRT.

@demo.mp4
Describe the clip over time, then locate when the presenter first opens the settings panel.

@workout.mp4
Count every completed push-up and list the timestamp of each repetition.
```

---

## Shared Case: local views, cloud grounding, and web verification

This Codex session locates cakes, annotates the image, identifies a photographed place, and verifies
the result on the web. The API part uses grounding and vision reasoning; local file/annotation work
belongs to [`core`](../core/usage.md#shared-case-local-views-cloud-grounding-and-web-verification),
and external verification belongs to
[`search`](../search/usage.md#shared-case-local-views-cloud-grounding-and-web-verification).

▶ **[View the shared detailed trace](https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen-MM-Plugins/asserts/core/case-core-codex-api-use.html)**

> The trace predates the capability split, so API calls appear under the old
> `qwen_mm_plugins_core` namespace. Today `grounding`, `ocr`, and `vision_chat` are provided by
> `qwen-mm-plugins-api`; the recorded inputs and outputs remain representative of the shared
> workflow.

<p align="center">
  <img src="../core/assets/codex-api-use.png" alt="Shared Core, API, and Search workflow" width="520">
</p>
