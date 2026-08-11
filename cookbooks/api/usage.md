# Cookbook — Qwen-MM-Plugins API

`qwen-mm-plugins-api` calls cloud models to *understand* media. Its tools are split by model family
into three subpackages (the directory is the category):

- **`vl/`** — Qwen-VL: `vision_chat`, `ocr`, `grounding` (image/video in, text or pixel boxes out).
- **`omni/`** — Qwen-Omni audio/video: transcription, diarization, temporal grounding, event counting,
  captioning, and music analysis. These tools reason over sampled frames and the embedded audio
  together; the ASR tools send only the extracted audio track.
- **`others/`** — `transcribe_audio` (Qwen3-ASR) and `segmentation` (self-hosted SAM3).

For whole-video QA over videos around 30 minutes or longer, use
[`video-memory`](../video-memory/usage.md) instead of the per-clip tools here. For local file reading
and visualization (no cloud call), see [`core`](../core/usage.md).

---

## Tools

**VL — `shared.api_openai`, DashScope**
- `vision_chat` — call a VLM (default: `qwen3.7-plus`) for vision chat over image / video input
- `ocr` — text recognition in images
- `grounding` — object detection/localization, returning pixel bboxes (pairs with core's `draw_bbox`)

**Omni — `shared.api_omni`, DashScope**

| Tool | Use it for | Main output |
|------|------------|-------------|
| `omni_asr` | Plain speech transcription without timing | One continuous text transcript |
| `omni_asr_timestamped` | Sentence- or word-level controllable ASR | Timestamped JSON segments and SRT |
| `omni_multi_speaker_asr` | Speaker diarization — who said what and when | Speaker-labelled segments and SRT |
| `omni_av_caption` | Describe what happens throughout a clip | Time spans with a description per span |
| `omni_av_grounding` | Find **when** a natural-language event appears | Matching start/end times |
| `omni_av_counting` | Count an event, object, or action | Total count and occurrence timestamps |
| `omni_music_caption` | Analyze a complete music track | Structured music tags and a dense English caption |

**Others**
- `transcribe_audio` — speech recognition (default: `qwen3-asr`), output as SRT / text / JSON
- `segmentation` — text-prompted segmentation (self-hosted SAM3)

Every tool accepts a local `file_path` or an HTTP(S)/OSS URL and supports `dry_run=true` to preview the
model request without calling the API. The Omni video tools also accept `fps` and `max_pixels`: raise
them only when finer temporal or visual detail is worth the extra latency and token cost.

`grounding` is spatial — it answers **where** something is inside a still image. `omni_av_grounding` is
temporal — it answers **when** something happens in a clip.

---

## Install

```bash
claude plugin marketplace add https://github.com/QwenLM/Qwen-MM-Plugins.git
claude plugin install qwen-mm-plugins-core@qwen-mm-plugins
claude plugin install qwen-mm-plugins-api@qwen-mm-plugins
```

---

## Requirements and configuration

| Requirement | Description |
|-------------|-------------|
| `DASHSCOPE_API_KEY` | Required — authenticates all Qwen-VL, Qwen-Omni, and Qwen3-ASR requests. |
| `DASHSCOPE_BASE_URL` | Optional — overrides the OpenAI-compatible endpoint for a proxy or gateway. |
| `QWEN_MM_AUDIO_RAW_B64` | Optional — set to `1` when `DASHSCOPE_BASE_URL` points at an OpenAI-spec server (e.g. vLLM): audio is sent as raw base64 instead of the DashScope-style `data:;base64,…` form, which such servers reject. Leave unset for DashScope. |
| `SAM3_SERVER_URL` | Required only for `segmentation` (self-hosted SAM3 server). |
| `ffmpeg` + `ffprobe` | Required for audio extraction, transcoding, and frame sampling/fitting. |
| `OSS_AK`, `OSS_SK`, `OSS_ENDPOINT`, `OSS_BUCKET` | Optional — upload oversized local video and pass a signed URL instead of local frame sampling. Install the `oss` extra as well. |

Set variables in the environment or `~/.qwen-mm-plugins/config`. The guided installer can write the
shared configuration and verify the system dependencies:

```bash
bash install.sh configure
bash install.sh verify
```

The default Omni model is `qwen3.5-omni-plus`; pass `model` to an individual tool to override it.

---

## Video delivery and OSS

Both video paths — `vision_chat` and the Omni tools — use the same switch: **if OSS is fully
configured, the local video is uploaded and a signed URL is passed to the model for server-side
sampling; otherwise it falls back to local frame extraction.** Configuring OSS lifts the local inline
limits (250 frames / ~40 minutes for `vision_chat`) and lets the server sample long inputs — up to the
model's server-side video-duration cap (e.g. qwen3.7-plus 2 h, Qwen3.5-Omni 1 h). A local file longer
than that cap skips the upload and degrades to local frame sampling (VL: frames; Omni: frames + audio)
— sparse for very long clips, but it still returns a result.

Without OSS:

- `vision_chat` samples frames locally, bounded by the 250-frame / ~40-minute inline limit; for longer
  videos use core's `read_video` or `video-memory`.
- The Omni tools fit one inline media item to the 10 MB base64 limit: audio that fits is sent
  unchanged, otherwise it is downmixed to 16 kHz mono (duration-fitted MP3 when needed); a short video
  is resized/transcoded to fit; a larger video falls back to sampled frames plus the full audio track,
  thinning frames until the request fits.
- An HTTP(S)/OSS URL is always fetched server-side and skips the local inline path.

`dry_run=true` never triggers a network upload — the OSS branch is shown as a placeholder.

---

## Example requests

```text
@receipt.jpg
OCR this receipt and total the line items.

@street.jpg
Draw a box around every car in the scene.

@meeting.mp4
Transcribe this meeting with speaker labels and sentence-level timestamps. Return SRT.

@demo.mp4
Describe the clip over time, then locate every segment where the presenter opens the settings panel.

@workout.mp4
Count every completed push-up and list the timestamp of each repetition.

@track.wav
Analyze the genre, moods, instruments, key, time signature, and vocal profile. Also write a compact
English caption that could be used as a music-generation prompt.
```

The tools work the same in Chinese — the prompt language mainly steers the wording of the answer:

```text
@会议录音.m4a
把这段录音转成文字，不需要时间戳。

@访谈.mp4
区分说话人并逐句标注时间，输出 SRT 字幕。

@产品演示.mp4
按时间顺序描述视频内容，并找出讲解人第一次展示价格页面的时间段。

@监控.mp4
数一下画面里一共出现了几辆电动车，并列出每次出现的时间点。

@片头音乐.mp3
分析这首曲子的风格、情绪、乐器、调性和节拍，再写一段可用于音乐生成的英文提示词。
```

---

## Cases

Each case below was recorded against a live DashScope endpoint: the **Call** shows the exact
arguments passed to the tool. Every tool returns the same shape — one JSON block carrying the
structured result, then a short plain-text summary — and the **Output** reproduces the returned
blocks verbatim. The clips live in [assets/](assets/); provenance and trimming notes are in
[assets/SOURCES.md](assets/SOURCES.md).

### Case 1 — transcribe and timestamp a short English clip (`omni_asr` + `omni_asr_timestamped`)

`guess_age_gender.wav` (9 s) — a single English question about guessing age/gender from voice.

**Call**

```python
omni_asr(
    file_path="assets/guess_age_gender.wav",
    language="en",
)
```

**Output** — JSON block, then summary block

```json
{ "text": "I heard that you can understand what people say and even know their age and gender. So can you guess my age and gender from my voice?" }
```

```text
I heard that you can understand what people say and even know their age and gender.
So can you guess my age and gender from my voice?
```

**Call** — sentence-level timestamps, SRT format

```python
omni_asr_timestamped(
    file_path="assets/guess_age_gender.wav",
    language="en",
    granularity="sentence",
    format="srt",
)
```

**Output** — JSON block (segment detail), then SRT block

```json
{
  "granularity": "sentence",
  "segments": [
    { "start": 0.647, "end": 5.387,
      "text": "I heard that you can understand what people say and even know their age and gender." },
    { "start": 5.907, "end": 9.017,
      "text": "So can you guess my age and gender from my voice?" }
  ]
}
```

```text
1
00:00:00,647 --> 00:00:05,387
I heard that you can understand what people say and even know their age and gender.

2
00:00:05,907 --> 00:00:09,017
So can you guess my age and gender from my voice?
```

### Case 2 — speaker diarization on a two-person interview (`omni_multi_speaker_asr`)

`interview_clip.wav` (35 s) — one interviewer, one interviewee; the tool splits the speech by
speaker without being told how many there are.

**Call**

```python
omni_multi_speaker_asr(
    file_path="assets/interview_clip.wav",
    format="json",
)
```

**Output** — JSON block (speaker detail), then SRT block

```json
{
  "speakers": ["Speaker 1", "Speaker 2"],
  "segments": [
    { "speaker": "Speaker 1", "start": 0.0,  "end": 26.38,
      "text": "you cut yourself but you don't mind and so you're not going to do a lot to avoid being cut again. So this region exists also in the rat and it's a relatively deep brain region so therefore we turn to the rat and figured in the rat we can change the activity in that brain region and see whether that would then change how much a rat would be averse to harming other rats or not." },
    { "speaker": "Speaker 2", "start": 29.87, "end": 32.95,
      "text": "Could you briefly explain this study and its findings?" },
    { "speaker": "Speaker 1", "start": 34.33, "end": 34.83, "text": "Ah." }
  ]
}
```

```text
1
00:00:00,000 --> 00:00:26,380
[Speaker 1] you cut yourself but you don't mind and so you're not going to do a lot to avoid being cut again. So this region exists also in the rat and it's a relatively deep brain region so therefore we turn to the rat and figured in the rat we can change the activity in that brain region and see whether that would then change how much a rat would be averse to harming other rats or not.

2
00:00:29,870 --> 00:00:32,950
[Speaker 2] Could you briefly explain this study and its findings?

3
00:00:34,330 --> 00:00:34,830
[Speaker 1] Ah.
```

### Case 3 — understand a video over time (`omni_av_caption`)

`draw1_clip.mp4` (15 s) — a tablet screen-recording of someone drawing a ukulele. The tool returns
per-span descriptions plus speaker/transcript, visible-text, and safety sections; it reads the
drawn object, the canvas UI, the artist's gestures, and the voice-over.

<p align="center">
  <img src="assets/case-video-caption.png" alt="draw1_clip.mp4 key frames" width="520">
</p>

**Call**

```python
omni_av_caption(
    file_path="assets/draw1_clip.mp4",
)
```

**Output** (abridged — full block is ~4,400 chars)

```text
## Storyline

00:00.000 – 00:02.500
... On the tablet's screen is a cartoon-style drawing of a small guitar-like instrument (ukulele
or acoustic guitar) ... At this moment a young female voice ... says, "Hello, take a look at what
I'm drawing." ...

00:02.500 – 00:06.500
... she traces and refines the existing black outline around the guitar's body, neck, and
headstock, making deliberate strokes ...

00:06.500 – 00:10.000
... she fills the interior of the guitar's body more completely with the same tan hue, smoothing
out any gaps ...

00:10.000 – 00:13.000
The artist taps an icon ... a vertical color-selection panel slides out ... Across the top of the
panel appears the Chinese word "颜色," meaning "Color." ...

00:13.000 – 00:15.000
The color panel retracts ... The video ends with the completed illustration still centered on the
screen ...

## Speakers and Transcript

Speaker profiles:
Artist/Narrator – Young adult female; clear diction, slight East-Asian accent ...

00:00.588 – 00:03.228
Speaker: Artist/Narrator
Content: "Hello, take a look at what I'm drawing."
```

### Case 4 — locate and count a temporal event (`omni_av_grounding` + `omni_av_counting`)

`basketball_clip.mp4` (20 s) — one made basket mid-clip. Grounding finds **when** the event
happens; counting reports **how many** and where each occurrence is.

<p align="center">
  <img src="assets/case-video-grounding.png" alt="basketball_clip.mp4 key frames" width="520">
</p>

**Call**

```python
omni_av_grounding(
    file_path="assets/basketball_clip.mp4",
    query="a player making a basket",
)
```

**Output** — JSON block, then summary block

```json
{
  "query": "a player making a basket",
  "matches": [
    { "start": 13.0, "end": 17.0, "score": 0.95,
      "reason": "The video shows a player shooting the basketball and it successfully going through the hoop." }
  ]
}
```

```text
1 matching segment(s) for 'a player making a basket'.
```

**Call**

```python
omni_av_counting(
    file_path="assets/basketball_clip.mp4",
    target="dunk or made basket",
)
```

**Output** — JSON block, then summary block

```json
{
  "target": "dunk or made basket",
  "count": 1,
  "occurrences": [ { "start": 14.0, "end": 16.0, "note": "dunk or made basket" } ]
}
```

```text
Counted 1 occurrence(s) of 'dunk or made basket'.
```

### Case 5 — analyze a music track (`omni_music_caption`)

`music_40s.wav` (27 s) — acoustic-folk song with a female voice. The tool returns structured tags
plus a dense English caption that can feed straight into a music-generation model.

**Call**

```python
omni_music_caption(
    file_path="assets/music_40s.wav",
)
```

**Output** — JSON block (structured tags), then summary block (caption)

```json
{
  "genre": ["folk", "acoustic", "singer-songwriter"],
  "moods": ["calm", "gentle", "reflective", "intimate"],
  "instruments": ["piano", "acoustic guitar", "xylophone"],
  "has_vocals": true,
  "vocal_language": "english",
  "vocal_gender": "female",
  "vocal_timbre": ["soft", "clear", "conversational"],
  "key": "c major",
  "time_signature": "4/4",
  "caption": "A gentle and reflective acoustic folk piece featuring a simple, repeating piano melody accompanied by the bright, percussive tones of a xylophone. An acoustic guitar enters with warm, strummed chords, creating an intimate and calming atmosphere. The arrangement is sparse and organic, focusing on the natural timbres of the instruments. A soft, clear female voice speaks conversationally over the music, adding a personal and narrative layer to the serene soundscape."
}
```

```text
A gentle and reflective acoustic folk piece featuring a simple, repeating piano melody accompanied by the bright, percussive tones of a xylophone. An acoustic guitar enters with warm, strummed chords, creating an intimate and calming atmosphere. The arrangement is sparse and organic, focusing on the natural timbres of the instruments. A soft, clear female voice speaks conversationally over the music, adding a personal and narrative layer to the serene soundscape.
```
