<!-- Internal workflow loaded by the root Video Translation Skill. -->
# Translation Authoring Workflow

Read `project.json` and the accepted transcript. Translate for spoken delivery rather than literal character
parity. Preserve names, intent, tone, and information; prefer concise natural phrasing that fits each interval
at a normal speaking rate.

Author dubbing units, not a mechanical copy of subtitle rows. Every transcript segment must be used exactly
once and in order, but adjacent segments may be merged when they have the same speaker and a combined delivery
sounds more natural. Merge especially when a fragment is too short for stable TTS, a sentence was split only
by subtitle layout, or one translation needs the neighboring clause for natural English. Record the decision
in `source_segment_ids` and `merge_reason`. Do not merge across speakers.

Match the translated delivery to the actual dubbing slot. Raw character counts are not comparable across
languages. Prefer concise, idiomatic speech and read the line aloud mentally at a natural pace. Treat validator
duration estimates as diagnostics, not as authority over meaning or performance. Shorten or expand wording
naturally before relying on audio acceleration.

Do not leave a substantially shorter translation merely because it technically fits. When the source speaks
through most of the interval but the estimated translation fills less than roughly 60%, preserve omitted detail,
expand terse wording naturally, or reconsider grouping. Preserve genuine dramatic pauses rather than filling
them mechanically; the Agent makes this decision from the source performance.

Choose the reference independently for every translated segment. Prefer that segment's own source speech:

1. Start with the current dubbing unit's corresponding Agent-reconciled source speech.
2. Keep it when the interval contains clean speech from only that speaker and has enough usable speech for
   cloning, normally at least 1.5 seconds.
3. If useful speech is shorter than about 1.5 seconds, extend through adjacent same-speaker source segments
   whose gap is at most 1.2 seconds, provided the interval contains no other speaker.
4. If that still cannot produce a clean reference, use the nearest clean same-speaker segment, preferring a
   similar recording environment; use the speaker's longest clean segment only as the final fallback.
5. Never choose another speaker, a music-dominated clip, or a fallback merely to imitate a different emotion.

The reference object records one or more `source_segment_ids`, the exact interval, and a concrete
`selection_reason`. Do not reuse one speaker reference for every line merely for convenience. Voice synthesis
uses the dubbing service's default emotion settings; never add emotion descriptions, strength, or vectors.

Write `plan/translation_plan.json` exactly as described in `pipeline-contract.md`, copying the source path,
hash, languages, transcript/VAD hashes, source mappings, timing, speakers, and text. Call
`validate_video_translation_plan`.
Translation-only stops after validation.

If rendering later reports that speech cannot fit within the maximum accepted speed-up, revise only the
affected translated text and validate again. Do not silently exceed the timing policy or overlap neighbors.
