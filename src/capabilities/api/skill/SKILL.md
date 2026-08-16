---
name: qwen-mm-plugins-api
description: "Cloud MCP tools for understanding media, by model family. VL model: vision_chat (caption/VQA), ocr, grounding (detect/locate objects). Omni model (reads frames + audio together): timestamped captioning, ASR (plain / controllable / multi-speaker diarized), temporal grounding, event counting, music captioning. Plus transcribe_audio (ASR) and segmentation (SAM3). Use when a question about an image/video/audio needs an external model, not just local reading."
---

# Qwen-MM-Plugins API

You have `qwen-mm-plugins-api` MCP tools available. They call external models/services to understand media, grouped by model family:

- **VL model** (Qwen-VL, OpenAI-compatible endpoint): `vision_chat`, `ocr`, `grounding`.
- **Omni model** (Qwen-Omni — reads video frames **and** the embedded audio track together, so one call reasons over both): `omni_asr`, `omni_asr_timestamped`, `omni_multi_speaker_asr`, `omni_av_caption`, `omni_av_grounding`, `omni_av_counting`, `omni_music_caption`.
- **Other services**: `transcribe_audio` (Qwen3-ASR), `segmentation` (a SAM3 server).

Prefer these over manual ffmpeg/ffprobe scripting. Check the `qwen-mm-plugins-api` tools in your tool list for full schemas and parameters.

## When to Use Which Tool

**VL model** (single images/videos, spatial reasoning):

- **Ask a VLM** about images/videos (caption, VQA, free-form) → `vision_chat`
- **Extract text** from an image → `ocr`
- **Detect/locate objects** in an image (bounding boxes, spatial WHERE) → `grounding`

**Omni model** (audio + video together, temporal reasoning; clips up to a few minutes):

- **Transcribe speech, plain text** → `omni_asr` (one continuous string, no timestamps)
- **Transcribe with timestamps** → `omni_asr_timestamped` (`granularity` = `sentence` or `word`; also returns SRT)
- **Who said what** → `omni_multi_speaker_asr` (diarization: speaker labels + timestamps + SRT; pass `num_speakers` if known)
- **Describe the content over time** → `omni_av_caption` (splits into spans, one description + start/end per span)
- **Find WHEN something happens** → `omni_av_grounding` (natural-language `query` → matching time segments; temporal localization)
- **Count how many times** an event/object/action occurs → `omni_av_counting` (`target` → total + per-occurrence timestamps)
- **Analyze / caption a music track** → `omni_music_caption` (whole-track tags — genre / moods / instruments / key / time signature / vocal profile — plus a dense English caption for music generation; audio-only, no timestamps)

**Other services**:

- **Segment objects** in an image (masks) → `segmentation`
- **Transcribe speech** from audio/video, fast and long-file friendly → `transcribe_audio`

## Tips

**Vision chat**: pass `images`/`videos` + `text` prompt. Model precedence is explicit `model` →
`QWEN_MM_API_VL_MODEL` → `qwen3.7-plus`. Use `dry_run=true` to inspect payloads.

**Grounding**: returns normalized boxes (0–1000). Set `return_img=true` to get the annotated image back, or draw them yourself with core's `draw_bbox`. Needs `DASHSCOPE_API_KEY`.

**ASR** (`transcribe_audio`): accepts audio or video, auto-chunks long files. Formats: `srt` (default), `text`, `json`. Needs `DASHSCOPE_API_KEY` (and `ffmpeg` to pull the audio track from a video).

**Segmentation**: needs a SAM3 server (`SAM3_SERVER_URL`). To stand one up, run `references/launch_sam3_server.py` (multi-GPU HTTP server; see its header for prerequisites).

**Omni tools**: every tool takes a local audio/video `file_path` (or an http/OSS URL) and supports `dry_run=true`. The AV tools (`caption`/`grounding`/`counting`) accept `fps` and `max_pixels` to trade temporal/spatial detail against token cost — raise `fps` only for fast/frequent events; keep `max_pixels` at the default (≈448²) unless fine detail matters. The ASR family sends only the (extracted) audio track, so it is cheaper on video input. Timestamps are seconds from the start. Pass `language` (e.g. `zh`, `en`) as a hint when known. Model precedence is explicit `model` → `QWEN_MM_API_OMNI_MODEL` → `qwen3.5-omni-plus`.

**Video delivery (VL & Omni)**: a local video is uploaded and sampled server-side (lifting the inline frame cap) when OSS is configured (`OSS_AK`/`OSS_SK`/`OSS_ENDPOINT`/`OSS_BUCKET` + the `oss` extra); otherwise it is sampled into inline frames. Server-side sampling has a per-model video-duration limit (e.g. qwen3.7-plus 2 h, Qwen3.5-Omni 1 h), so a local file longer than that skips the upload and degrades to local frame sampling (VL: frames; Omni: frames + audio) — sparse for very long clips, but it still returns a result.

## Choosing between the families (do NOT overlap)

- **`transcribe_audio` vs `omni_asr*`**: `transcribe_audio` uses the dedicated Qwen3-ASR service (fast, chunks long files, 27 languages) — cheapest for a straight, long-file transcription. Pick the `omni_asr*` tools when you want Omni's understanding: multi-speaker diarization, controllable word/sentence granularity, or transcription fused with visual context.
- **`grounding` (spatial, WHERE) vs `omni_av_grounding` (temporal, WHEN)**: `grounding` draws a bounding box in a single image; `omni_av_grounding` locates a span in time. Different axes — don't substitute one for the other.
- **`vision_chat` vs the Omni AV tools**: `vision_chat` is a general VLM over images/video frames (no audio); the Omni tools fuse frames with the audio track and return structured, timestamped output. Use Omni when audio or precise timing matters.

## Relationship to Other Capabilities (do NOT overlap)

- **Read/visualize local files** (images, video frames, PDF, Office, 3D, ...) → `qwen-mm-plugins-core` (`read_image`/`read_video`/`visualize`/`crop`/`draw_bbox`/`save_view`).
- **Confirm a fact or identify an entity** (reverse image / web) → `qwen-mm-plugins-search` (`image_search`/`web_search`/`web_extractor`).
- **Long videos (30 min+)**: for whole-video QA over long content, use the `qwen-mm-plugins-video-memory` skill (hierarchical graph memory) instead of feeding the entire file to these per-call tools.
