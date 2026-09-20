<!-- Internal workflow loaded by the root Video Translation Skill. -->
# Source Analysis Workflow

Call `prepare_video_translation_project`, then obey the derived `analysis_mode` in the returned state. Use the
bundled `omni_call` with its normal Qwen3.5 Plus configuration and OpenAI-compatible interface; do not replace
it with the legacy Gemini request path and do not override the model merely to imitate an older workflow.

The mode comes directly from `project.json.duration_sec` and is not stored separately:

- `single_full_video`: the source is at most 600 seconds. Call the bundled `omni_call` exactly once with the
  complete source video and embedded audio. Do not create analysis clips or split the request into windows.
  Request visible subtitle OCR, spoken text in any detected language, speaker labels, and approximate absolute
  timestamps for the complete video.
- `bounded_windows`: the source is longer than 600 seconds. Use bounded, ordered Omni windows, preserve the
  requested or detected source language, and reconcile speaker labels across windows using appearance, voice,
  dialogue continuity, and overlaps.

The 600-second boundary is inclusive: a video of exactly 10 minutes uses one full-video request.

When the video has no visible subtitles, rely on spoken transcription rather than inventing OCR.

Omni timestamps and VAD boundaries are both fallible evidence. VAD is mandatory for every accepted transcript,
but it is not the timing authority:

1. extract the source audio and call `separate_dubbing_audio`;
2. call `detect_dubbing_speech` on the separated vocals;
3. write the returned evidence to `analysis/vad.json` using the schema below;
4. reconcile every Omni transcript item against VAD, the actual audible boundaries, subtitle timing, dialogue
   order, and speaker continuity;
5. let the Agent author the final `start_sec` and `end_sec` from the combined evidence.

VAD reports speech presence, not sentence boundaries or speaker identity. Do not mechanically turn every VAD
interval into one subtitle, snap every Omni boundary to the nearest VAD edge, or assume either source is exact.
When Omni and VAD disagree, inspect the source around the disputed boundary and prefer the timing that matches
audible speech and semantic continuity. The Agent decides whether an interval contains one utterance, several
utterances, or a pause inside one utterance. A low VAD-overlap diagnostic requires review, not automatic
rejection, because VAD may miss quiet, overlapped, or separation-damaged speech.

```json
{
  "schema": "omni-chatcut/video-translation-vad/v1",
  "source_movie": "/absolute/source.mp4",
  "source_sha256": "...",
  "duration_sec": 30.5,
  "audio_source": "work/source/vocals.wav",
  "parameters": {"threshold": 0.5, "min_speech": 0.2, "min_silence": 0.3, "pad": 0.1},
  "segments": [{"start_sec": 1.2, "end_sec": 5.8}]
}
```

Write `analysis/transcript.json` as an object containing source identity, language, evidence windows, and
ordered source-evidence segments with `segment_id`, `speaker`, Agent-reconciled `start_sec` and `end_sec`, and
`source_text`.
Preserve natural sentence and performance boundaries instead of chopping solely at subtitle changes. Preserve
uncertainty and block on unresolved speaker or text evidence that would materially change the dub. These are
source facts; translation authoring may merge adjacent same-speaker items into better dubbing units without
rewriting this evidence.

Analysis-only stops after the transcript has been reviewed.
