---
name: qwen-mm-plugins-api
description: "Understand images, video, and audio using hosted or self-hosted model services through MCP tools. Use for visual questions, OCR, object grounding, speech transcription, speaker diarization, timestamped captions, event localization/counting, music captioning, or segmentation. Includes VL and Omni model tools, transcribe_audio (Qwen3-ASR), and segmentation (SAM3)."
---

# Media Understanding

Use `qwen-mm-plugins-api` to understand media through configured model services. The tools are grouped by model family:

- **VL model** (Qwen-VL, OpenAI-compatible endpoint): `vision_chat`, `ocr`, `grounding`.
- **Omni model** (Qwen-Omni — the AV tools combine video frames and audio; the ASR/music tools focus on audio): `omni_asr`, `omni_asr_timestamped`, `omni_multi_speaker_asr`, `omni_av_caption`, `omni_av_grounding`, `omni_av_counting`, `omni_music_caption`.
- **Other services**: `transcribe_audio` (Qwen3-ASR), `segmentation` (a SAM3 server).

Check the `qwen-mm-plugins-api` tools in your tool list for full schemas and parameters. For file reading, rendering, or metadata inspection, use `core`.

## When to Use Which Tool

**VL model** (single images/videos, spatial reasoning):

- **Ask a VLM** about images/videos (caption, VQA, free-form) → `vision_chat`
- **Extract text** from an image → `ocr`
- **Detect/locate objects** in an image (bounding boxes, spatial WHERE) → `grounding`. It sends EXIF-corrected pixels, so returned 0–1000 boxes address the displayed image and can be passed directly to core `crop`/`draw_bbox` or search `image_search`.

**Omni model** (audio + video together, temporal reasoning; clips up to a few minutes):

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

**Omni tools**: every tool takes a local audio/video `file_path` (or an http/OSS URL) and supports `dry_run=true`. The AV tools (`caption`/`grounding`/`counting`) accept `fps` and `max_pixels` to trade temporal/spatial detail against token cost — raise `fps` only for fast/frequent events; keep `max_pixels` at the default (≈448²) unless fine detail matters. The ASR family extracts and sends only the audio track from local video; remote video URLs are passed through for server-side handling. Timestamps are seconds from the start. For ASR, pass `language` (e.g. `zh`, `en`) as a hint when known. Model precedence is explicit `model` → `QWEN_MM_API_OMNI_MODEL` → `qwen3.5-omni-plus`.

**Video delivery**: VL uploads local video when OSS is configured and the model's duration limit allows it; otherwise it samples inline frames. Omni first fits local video into an inline media item, then uses OSS or sampled frames plus audio if needed. Video over the model's server-side duration limit uses local sampling. Very long audio can still exceed the inline budget. OSS requires `OSS_AK`/`OSS_SK`/`OSS_ENDPOINT`/`OSS_BUCKET` and the `oss` extra.

## Choosing between the families (do NOT overlap)

- **`transcribe_audio` vs `omni_asr*`**: `transcribe_audio` uses the dedicated Qwen3-ASR service and chunks long files. Pick the `omni_asr*` tools for multi-speaker diarization or controllable word/sentence granularity. Local video inputs to the ASR tools are reduced to their audio track; use the Omni AV tools when visual context matters.
- **`grounding` (spatial, WHERE) vs `omni_av_grounding` (temporal, WHEN)**: `grounding` draws a bounding box in a single image; `omni_av_grounding` locates a span in time. Different axes — don't substitute one for the other.
- **`vision_chat` vs the Omni AV tools**: `vision_chat` is a general VLM over images/video frames (no audio); the Omni AV tools combine frames with the audio track for timestamped descriptions, localization, or counting. Use Omni when audio or precise timing matters.

## Relationship to Other Capabilities (do NOT overlap)

- **Read/visualize local files** (images, video frames, PDF, Office, 3D, ...) → `qwen-mm-plugins-core` (`read_image`/`read_video`/`visualize`/`crop`/`draw_bbox`/`save_view`).
- **Confirm a fact or identify an entity** (reverse image / web) → `qwen-mm-plugins-search` (`image_search`/`web_search`/`web_extractor`).
- **Long videos (30 min+)**: for whole-video QA over long content, use the `qwen-mm-plugins-video-memory` skill (hierarchical graph memory) instead of feeding the entire file to these per-call tools.
