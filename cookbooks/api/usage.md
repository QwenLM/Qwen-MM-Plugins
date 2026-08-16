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
