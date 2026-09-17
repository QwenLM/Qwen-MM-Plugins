# Execution Contract

## Source of truth

The validated storyboard and source audio are immutable execution inputs. The executor may translate storyboard fields into provider prompts and payloads, but it must not rewrite story, cast, scenes, lyrics, timing, actions, camera intent, or transitions.

## Reference model

Video-provider requests contain:

1. generated identity portraits for Wan, or configured Ark image asset IDs for Seedance, for the shot cast
2. the complete shot audio interval

The saved prompt must explicitly map every reference number:

- person references control identity only
- the single-shot prompt directly creates the complete environment and controls cast presence, wardrobe, blocking, actions, expressions, camera, and mouth state

Never generate or submit per-shot images containing people. Never anonymize a human keyframe and pass the resulting blur or mosaic to a video provider.

## One-shot request contract

Every segment contains exactly one `sub_shot` and one `shot_window` spanning local time
`[0, segment.duration_sec]`. Segment timing, scene, and cast exactly match that shot. The prompt includes:

- local start and end seconds
- scene name and the complete setting/layout/light/palette/material definition
- exact cast allowed in the window
- person asset reference numbers
- wardrobe from the active scene
- blocking, facing, action, and expression
- camera and subject-camera relationship
- lyric and mouth-state rules
- storyboard-declared edit-anchor source, timestamp, evidence, and selection reason; accepted sources are track start, structure boundary, lyric-phrase boundary, or the documented structure-internal split exception
- the continuous physical or visual end state handed to local assembly

The request also carries the shot's direct `visual_design` and `action_design`; dance and performance
shots additionally carry `movement_design` or `performance_design`. It is self-contained apart from
canonical cast, scene, style, and audio inputs.

The prompt includes concise whole-film and visual-unit context from `creative_direction`, followed by
the shot's literal instructions. The context preserves development across independently generated
shots but cannot override the shot's exact cast, scene, action, camera, timing, or mouth state.

Every shot uses its complete declared environment. One request cannot contain multiple scenes or editorial camera setups.

The selected provider produces one continuous editorial shot. The prompt must not ask it to insert a hard cut,
dissolve, match cut, another camera setup, or another scene. Every inter-shot boundary is created by
the local assembler from separate generated files. A shot at or above the storyboard's long-take
threshold requires an intentional-long-take rationale and at least two lyric- or structure-grounded
internal musical stages; these stages describe continuous development, not edits.

## Audio and duration

- Send the complete shot WAV through the selected adapter's reference-audio transport. Wan uses a remote URL; Seedance uses an inline audio data URL that is redacted from saved request artifacts.
- Use the selected adapter's integer provider duration. Wan uses `max(2, ceil(shot.duration_sec))`.
  Seedance reference-video mode uses `min(12, max(4, ceil(shot.duration_sec)))`; shorter shots are
  generated at 4 seconds and trimmed, while shots in `(12, 13]` seconds are generated at 12 seconds
  and stretched. Both are normalized locally to the exact storyboard frame count.
- Enforce the selected adapter's duration limit before submission: Wan reference audio is 1–15 seconds;
  Seedance accepts storyboard shots up to 13 seconds under the normalization rule above.
- Normalize each downloaded result to its exact storyboard frame allocation. Measure the video
  stream duration; if it is shorter than the target frame duration, slow the whole video uniformly
  to fit. Trim longer videos at their original speed. Do not fill a short shot by holding its last frame.
- Use the untouched complete source audio as the final soundtrack.

## Scheduling and resume

Video execution preserves three explicit states:

1. submit every remaining segment using the selected provider's configured rate ladder, lowering speed only after widespread rate-limit evidence
2. poll all submitted tasks
3. download completed tasks immediately after success because provider result URLs may expire

A transient 429 requeues only the affected segment. Do not globally reduce configured RPM from a single response. Every selected task must eventually reach `succeeded`; retry or stop explicitly, never skip.

Persist an execution signature derived from provider name, provider schema, endpoint, model, generation parameters, duration, media hashes, and prompt. Reuse a local video only when `output_signature` matches the current execution signature.

## Assembly

Normalize every shot to the configured size, frame rate, and exact frame count. Concatenate those
files at their storyboard boundaries. Four-frame boundary softening is the default
(`boundary_blend_frames=4`, about 0.167 seconds at 24 fps). It prepends cloned frames to the incoming
shot and overlaps only those padding frames, preserving the total timeline frame count. Set
`boundary_blend_frames=0` for exact hard cuts. The final audio is never crossfaded or shortened.

## Review gates

Before submission, verify from storyboard data and generated dependencies:

- complete, prompt-ready scene descriptions for every shot
- fictional identity portraits for Wan, or Ark image asset bindings for Seedance
- one-shot request mapping and reference order
- exact per-shot cast, wardrobe, actions, camera, audio interval, and duration

When `quality_control.enabled=true`, `omni_call` reviews each generated shot video together with its exact original-audio interval and produces one structured rating. The local executor applies the configured policy; Omni never authorizes generation or changes retry ceilings.

When explicitly enabled, semantic QC evaluates:

- identity fidelity and character count
- anatomy and acting
- dance-style fidelity, connected movement density, accent response, and expressive commitment
- excessive ordinary walking, static posing, generic arm gestures, or prolonged holds inside dance/performance windows
- unwanted internal cuts or scene changes
- lip sync
- unwanted text, subtitles, logos, watermarks, or privacy masks

Semantic QC is disabled by default and profiles do not enable it. The per-run
`quality_control.enabled` value is the sole switch. When it is `true`, the default `reject_major`
strategy accepts `fully_compliant` and `minor_issues`, regenerates that
individual shot when it has `major_issues`, and retains no more than three total candidates. When all
configured rounds are rejected, select the best candidate by categorical rating, then fewest explicit
major and minor issue records; an exact tie favors the earlier round. Mark it `fallback_selected`
rather than pretending it passed. `accept_all` records the first candidate's rating without semantic
rejection. Configuration may override the strategy, round count, prompt, model, sampling, and
project-specific requirements.

Technical QC proves media properties only. The default disabled semantic-QC state is reported as
skipped, not passed, and makes no Omni shot-review or review-triggered regeneration calls.
