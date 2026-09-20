<!-- Internal workflow loaded by the root Video Translation Skill. -->
# Dubbing Rendering Workflow

Call `check_dubbing_service` before production. Call `render_video_translation` only with a validated plan.

Before the final render, inspect the one full-length separated background stem instead of assuming it
contains BGM:

1. Extract the complete source audio to `work/source/source.wav` as 48 kHz stereo PCM, then call
   `separate_dubbing_audio` once with `output_dir=work/source`. The tool writes a separation signature so the
   renderer reuses these exact full-length stems instead of running Demucs again.
2. Run FFmpeg `volumedetect` once on the complete `work/source/no_vocals.wav`. If it is effectively silent
   (`max_volume=-inf`, or both
   `mean_volume <= -55 dBFS` and `max_volume <= -45 dBFS`), select `background_mode=omit`.
3. Otherwise read `references/background-audio-review-prompt.md` and call `omni_call` on the complete stem.
   Use its decision, but default to `include` on malformed output, low confidence, or uncertainty.
4. Pass the selected `background_mode` to `render_video_translation`. Do not create additional analysis
   artifacts unless the user asks for diagnostics. Never repeat BGM detection per dubbing segment.

The renderer deterministically:

1. extracts source audio and separates vocals/background;
2. cuts the exact Agent-selected per-unit reference clips;
3. synthesizes translated speech with IndexTTS2;
4. borrows at most 0.3 seconds from each neighboring silent gap;
5. preserves the natural pace of short synthesized speech, centers remaining silence on both sides, applies
   only bounded acceleration up to `1.18x` for long speech, and adds short boundary fades to prevent clicks;
6. converts fitted speech and any retained background to a 48 kHz stereo production bus;
7. leaves fitted-line loudness unchanged before timeline assembly;
8. places speech on the original timeline and, only when `background_mode=include`, mixes voices with
   background using `amix normalize=0`, without automatic sidechain ducking;
9. applies measured two-pass EBU R128 normalization only once, to the final full-length mix, at `-16 LUFS`,
   `-1.5 dBTP`, `11 LRA`;
10. copies the source video stream, replaces audio, and preserves compatible subtitle streams;
11. writes a content-hashed render report and measured technical QA.

If a translated utterance is still too long at `1.18x`, return to translation authoring and shorten it. Do not
clip speech or overlap adjacent segments. Reuse successful raw TTS only when the plan and reference remain
unchanged; otherwise render with reuse disabled.

Treat an overlong TTS result as generation variance before blaming the translation. When the estimated spoken
duration requires at most `1.35x` speed, generate at most three candidates for that one dubbing unit and accept
the first natural candidate that fits within the `1.18x` renderer limit. Count an overlong cached candidate as
the first attempt, so at most two new calls are made on resume. Do not regenerate other successful units. When
the estimate already exceeds `1.35x`, or all allowed candidates remain too long, stop and shorten the translated
text. Do not choose a clipped or audibly rushed candidate merely because it is shorter.

Technical success is not completion. Review `full/translated.mp4` with the default Qwen3.5 Plus `omni_call`
and inspect `full/render_report.json`. Pay special attention to lines with acceleration above `1.12x`, very
short references, merged source segments, abrupt pauses, voice identity drift, untranslated meaning, overlap,
or background masking. The Agent decides whether each issue needs a shorter or fuller translation, a better
grouping, a different same-speaker reference, or another TTS pass. When speech fills less than 60% of a slot,
check whether meaning was omitted or the source contains an intentional dramatic pause. Do not hide a quality
problem by changing playback speed merely to fill time.

When the result is acceptable, write `full/agent_review.json` as specified by `pipeline-contract.md`. Finish by
calling `validate_video_translation_delivery`. Delivery requires a full decode, source-duration agreement,
exact segment accounting, matching hashes, and a passing Agent quality review.
