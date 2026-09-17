# Pipeline Contract

## Durable artifacts

```text
project.json
analysis/transcript.json
analysis/vad.json
plan/translation_plan.json
work/source/{source,vocals,no_vocals}.wav
work/references/DUB_NNNN.wav
work/tts_raw/DUB_NNNN.wav
work/tts_fitted/DUB_NNNN.wav
full/translated.mp4
full/render_report.json
full/translation_diagnostics.json
full/translation_summary.md
full/final_qa.json
full/agent_review.json
```

Audio stages intentionally use different formats: source/separated audio remains at its high-quality working
format, references are 24 kHz mono for the TTS service, and raw TTS remains service-native. Fitted speech,
background, the mix bus, and final translated audio use 48 kHz stereo. Resampling does not invent TTS detail;
the 48 kHz bus avoids unnecessary mixed-rate processing and matches normal video production delivery.

Changing the source invalidates every downstream artifact. Changing VAD evidence, transcript text, speakers,
translation, grouping, timing, or reference selection invalidates the affected speech renders and delivery.

## Source-analysis policy

The analysis mode is derived directly from `project.json.duration_sec`; it is not stored separately. Videos
whose measured duration is at most 600 seconds use `single_full_video` and recognize speech/subtitles in any
detected language. Longer videos use `bounded_windows` while preserving the requested or detected source
language. Exactly 600 seconds belongs to the single-request path.

## Plan schema

`analysis/transcript.json` must bind its segments to the source:

```json
{
  "source_movie": "/absolute/source.mp4",
  "source_sha256": "...",
  "source_language": "zh",
  "segments": [
    {
      "segment_id": "SEG_0001",
      "speaker": "SPK_001",
      "start_sec": 1.2,
      "end_sec": 3.8,
      "source_text": "..."
    }
  ]
}
```

`analysis/vad.json` is mandatory and uses `omni-chatcut/video-translation-vad/v1`. Its ordered intervals are
speech-activity evidence, not authoritative transcript boundaries. The Agent reconciles Omni timestamps and VAD
with audible speech, subtitle evidence, dialogue order, semantics, and speaker continuity. Low VAD overlap is a
review diagnostic rather than a validation failure because either detector may be wrong.

Plan segments are Agent-authored dubbing units. Together their `source_segment_ids` must consume every
accepted transcript segment exactly once and in order. One dubbing unit may merge adjacent same-speaker source
segments; its interval spans the first source start through the last source end. Dubbing units must not overlap.
A reference interval must contain evidenced speech from the selected speaker and no speech from another speaker.

`plan/translation_plan.json` uses `omni-chatcut/video-translation-plan/v2`:

```json
{
  "schema": "omni-chatcut/video-translation-plan/v2",
  "source_movie": "/absolute/source.mp4",
  "source_sha256": "...",
  "source_language": "zh",
  "target_language": "en",
  "transcript_sha256": "...",
  "vad_sha256": "...",
  "segments": [
    {
      "segment_id": "DUB_0001",
      "source_segment_ids": ["SEG_0001", "SEG_0002"],
      "merge_reason": "one spoken sentence split across two subtitle cards",
      "speaker": "SPK_001",
      "start_sec": 1.2,
      "end_sec": 5.1,
      "source_text": "第一句，第二句。",
      "translated_text": "One natural translated utterance.",
      "reference": {
        "source_segment_ids": ["SEG_0001", "SEG_0002"],
        "start_sec": 1.2,
        "end_sec": 5.1,
        "selection_reason": "corresponding clean source delivery"
      }
    }
  ]
}
```

Plan IDs are contiguous `DUB_NNNN`. By default, each reference uses that unit's corresponding Agent-reconciled
source speech. For a short reference, the Agent may include adjacent same-speaker source segments no more than
1.2 seconds apart. A fallback may use another clean same-speaker segment when the corresponding speech is too
short, noisy, overlapped, clipped, or badly separated. `selection_reason` records the judgment.

Translation must preserve meaning while fitting the available speaking duration; character count alone is
not a timing contract. Duration estimates are advisory. The actual natural TTS duration is authoritative and
the renderer refuses speech that would require acceleration above `1.18x`. Short speech remains at its natural
pace; any remaining silence is split evenly before and after the speech. A low slot-fill ratio is surfaced for
Agent review so omitted meaning or poor grouping can be fixed instead of disguising the gap with slow playback.
Per-line fitting does not normalize loudness. Measured two-pass EBU R128 normalization is applied only after
the complete voice bus and optional background have been mixed.

An overlong TTS candidate does not immediately invalidate a translation unless the estimated speech already
requires more than `1.35x` speed. Otherwise the renderer may generate up to three candidates for that one unit,
including an overlong cached candidate, and accepts the first candidate within the `1.18x` limit. Exhausting
the candidate budget requires translation shortening; it must not trigger regeneration of unrelated units.

## Completion

The renderer preserves the source video stream, replaces the audio with separated background plus translated
speech, records source, plan, and output hashes, and writes required QA checks.
Technical QA is not listening QA. Before validation, the Agent reviews the rendered video with the default
Qwen3.5 Plus `omni_call`, inspects every high-risk line in `render_report.json`, and writes
`full/agent_review.json` using `omni-chatcut/video-translation-agent-review/v1`. It binds the output and plan
hashes, lists reviewed `DUB_NNNN` IDs, records concrete issues, and supplies boolean checks for `translation`,
`timing`, `voice_reference`, `natural_delivery`, and `mix`. Set `overall_pass=true` only when every check passes.

The renderer also writes `full/translation_diagnostics.json` and `full/translation_summary.md`. These are
user-facing diagnostics, not replacements for Agent listening QA: the summary lists automatically flagged
segments first, with their time ranges, source/translated text, reference path, and a plain-language reason
for manual review. The tool response must surface the same review list and artifact paths directly.

`validate_video_translation_delivery` performs an independent ffprobe and full decode and verifies this review;
report booleans alone cannot establish success.
