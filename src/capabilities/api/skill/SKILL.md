---
name: qwen-mm-plugins-api
description: "Understand images, video, and audio using hosted or self-hosted model services through MCP tools. Use for visual questions, OCR, object grounding, speech transcription, speaker diarization, timestamped captions, event localization/counting, music captioning, or segmentation. Includes VL and Omni model tools, general-purpose Omni chat, transcribe_audio (Qwen3-ASR), and segmentation (SAM3)."
---

# Media Understanding

Use `qwen-mm-plugins-api` to understand media through configured model services. The tools are grouped by model family:

- **VL model** (Qwen-VL, OpenAI-compatible endpoint): `vision_chat`, `ocr`, `grounding`.
- **Omni model** (Qwen-Omni — combines video frames and audio): `perceive_media`, `omni_asr`, `omni_asr_timestamped`, `omni_multi_speaker_asr`, `omni_av_caption`, `omni_av_grounding`, `omni_av_counting`, `omni_music_caption`.
- **Other services**: `transcribe_audio` (Qwen3-ASR), `segmentation` (a SAM3 server).

Check the `qwen-mm-plugins-api` tools in your tool list for full schemas and parameters. For file reading, rendering, or metadata inspection, use `core`.

## When to Use Which Tool

**VL model** (single images/videos, spatial reasoning):

- **Ask a VLM** about images/videos (caption, VQA, free-form) → `vision_chat`
- **Extract text** from an image → `ocr`
- **Detect/locate objects** in an image (bounding boxes, spatial WHERE) → `grounding`. It sends EXIF-corrected pixels, so returned 0–1000 boxes address the displayed image and can be passed directly to core `crop`/`draw_bbox` or search `image_search`.

**Omni model** (audio + video together, temporal reasoning):

- **Prefer general-purpose chat** → `perceive_media`. Pass the actual objective in `prompt`; use
  `audio`, `video`, or `auto` according to the evidence needed.
- Treat the atomic tools below as references. Use one when its fixed structured output is explicitly
  needed rather than merely because its name resembles the task.
- **Transcribe speech, plain text** → `omni_asr` (one continuous string, no timestamps)
- **Transcribe with timestamps** → `omni_asr_timestamped` (`granularity` = `sentence` or `word`; also returns SRT)
- **Who said what** → `omni_multi_speaker_asr` (diarization: speaker labels + timestamps + SRT; pass `num_speakers` if known)
- **Describe the content over time** → `omni_av_caption` (timestamped Markdown descriptions of visual content, dialogue, music, and sounds)
- **Find WHEN something happens** → `omni_av_grounding` (natural-language `query` → matching time segments; temporal localization)
- **Count how many times** an event/object/action occurs → `omni_av_counting` (`target` → total + per-occurrence timestamps)
- **Analyze / caption a music track** → `omni_music_caption` (whole-track tags — genre / moods / instruments / key / time signature / vocal profile — plus a dense English caption for music generation; audio-only, no timestamps)

**Other services**:

- **Segment objects** in an image (masks) → `segmentation`
- **Transcribe speech** from audio/video, fast and long-file friendly → `transcribe_audio`

## Tips

**Vision chat**: pass `images`/`videos` + `text` prompt. Model precedence is explicit `model` →
`QWEN_MM_API_VL_MODEL` → `qwen3.7-plus`. Use `dry_run=true` to inspect payloads.

**VL/Omni endpoints**: default to DashScope (`DASHSCOPE_BASE_URL` and `DASHSCOPE_API_KEY`). Use the existing `base_url`, `api_key`, and `model` arguments for another compatible endpoint, including a self-hosted service. The endpoint must support the selected tool's media payload and model.

**OrcaRouter**: configure `ORCAROUTER_API_KEY`, then pass `base_url="https://api.orcarouter.ai/v1"` and a gateway `model` ID. The server selects that key automatically; an explicit `api_key` overrides it.

**OpenRouter**: configure `OPENROUTER_API_KEY`, then pass `base_url="https://openrouter.ai/api/v1"` and an OpenRouter `model` ID, such as `qwen/qwen3.7-plus` for images. The server selects that key automatically; an explicit `api_key` overrides it.
For video, use `videos` with a suitable model such as `qwen/qwen3.8-max-0902`. Local sampled frames
are sent as ordered images; direct video URLs require video support from the model and provider.

**Grounding**: returns normalized boxes (0–1000). Set `return_img=true` to get the annotated image back, or draw them yourself with core's `draw_bbox`.

**ASR** (`transcribe_audio`): accepts audio or video, auto-chunks long files. Formats: `srt` (default), `text`, `json`. Uses DashScope with `DASHSCOPE_API_KEY`; configured `ASR_SERVER_URLS` provide a self-hosted fallback when the key is absent or DashScope fails. Needs `ffmpeg` for audio extraction and chunking.

**Segmentation**: needs a SAM3 server (`SAM3_SERVER_URL`). To stand one up, run `references/launch_sam3_server.py` (multi-GPU HTTP server; see its header for prerequisites).

**Omni tools**: every tool takes a local audio/video `file_path` (or an http/OSS URL) and supports `dry_run=true`. Prefer `perceive_media`; it sends the supplied prompt unchanged. For long audio or video, call it repeatedly over different local `start_time`/`end_time` ranges: use a broad pass to find likely intervals, then inspect those intervals more closely, with overlap when an event may cross a boundary. A selected interval is rebased to 0 for model-visible timestamps; add `start_time` to map a returned timestamp back to the original file. Ranged results include the requested and actually processed intervals, including whether the end was clamped to the media duration. The AV tools accept `fps` and `max_pixels` to trade temporal/spatial detail against token cost — raise `fps` only for fast/frequent events; keep `max_pixels` at the default (≈448²) unless fine detail matters. The ASR family extracts and sends only the audio track from local video; remote video URLs are passed through for server-side handling. Timestamps are seconds from the start. For ASR, pass `language` (e.g. `zh`, `en`) as a hint when known. Model precedence is explicit `model` → `QWEN_MM_API_OMNI_MODEL` → `qwen3.5-omni-plus`.

**Video delivery**: VL and Omni walk the same ladder for oversized local media. On a DashScope endpoint the file goes to the model-bound temporary OSS first (up to 1 GiB; currently retained for about 48 hours), then to user-managed OSS, and only then to local sampling — inline frames for VL, inline fitting or frames plus audio for Omni. An oversized local image file takes the same upload path (`grounding` is exempt: it must send the exact pixels it measures boxes against, so it always inlines). Video over the model's server-side duration limit skips both uploads and uses local sampling. User-managed OSS requires `OSS_AK`/`OSS_SK`/`OSS_ENDPOINT`/`OSS_BUCKET` and the `oss` extra; temporary DashScope OSS only requires the same DashScope API key and model used for inference.

## Choosing between the families (do NOT overlap)

- **`transcribe_audio` vs `omni_asr*`**: `transcribe_audio` uses the dedicated Qwen3-ASR service and chunks long files. Prefer `perceive_media` for Omni tasks; use `omni_asr*` when a fixed transcript/SRT/diarization schema is explicitly required. Local video inputs to the ASR tools are reduced to their audio track.
- **`grounding` (spatial, WHERE) vs `omni_av_grounding` (temporal, WHEN)**: `grounding` draws a bounding box in a single image; `omni_av_grounding` locates a span in time. Different axes — don't substitute one for the other.
- **`vision_chat` vs Omni**: `vision_chat` handles images/video frames without audio; prefer `perceive_media` when audio or joint audio-video evidence matters.

## Relationship to Other Capabilities (do NOT overlap)

- **Read/visualize local files** (images, video frames, PDF, Office, 3D, ...) → `qwen-mm-plugins-core` (`read_image`/`read_video`/`visualize`/`crop`/`draw_bbox`/`save_view`).
- **Confirm a fact or identify an entity** (reverse image / web) → `qwen-mm-plugins-search` (`image_search`/`web_search`/`web_extractor`).
- **Long videos (30 min+)**: use repeated, focused `perceive_media` calls for targeted analysis; use `qwen-mm-plugins-video-memory` when reusable whole-video memory or broad retrieval is needed.
