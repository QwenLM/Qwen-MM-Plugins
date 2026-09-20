# Final Caption Synthesis

Consume exactly one validated `music-caption-evidence` object. Produce one readable,
evidence-grounded caption without adding facts absent from that object.

## Evidence roles

1. `audio` and `structure` define the authoritative structure timeline and section order.
2. `segment_annotations` supplies local vocal, instrumentation, performance, and production
   evidence for the matching structure segment only.
3. `sentence_lyrics` is an independent, verbatim SRT timeline. Never nest it under structure,
   assign cues to sections, split cues at boundaries, or use it to repair structure.
4. `global_caption` supplies detailed whole-track evidence. Preserve its distinct, supported musical
   information in the Overview and Overall Summary without assigning global observations to specific
   structure intervals. Retain approximate values, alternatives, and uncertainty qualifiers.
5. `structure_asr_lyrics` is secondary diagnostic evidence only. Do not merge it into the printed
   sentence-level lyrics.

When branches conflict, preserve both independent observations without inventing agreement. Do not
call a section vocal or instrumental solely from SRT overlap; use its matched clip annotation for the
section caption. Do not silently move timestamps.

## Required output

Output exactly these four top-level sections:

````text
## 1) Whole-Track Overview

## 2) Structure Timeline

Analysis note: optional one-line summary of deterministic short-section merges or final-end clamping

### [HH:MM:SS,mmm --> HH:MM:SS,mmm] [label]
Evidence-rich local description preserving this section's defining traits and supported changes

## 3) Lyrics Timeline

1
HH:MM:SS,mmm --> HH:MM:SS,mmm
verbatim lyric text

## 4) Overall Summary

### Emotional and Energy Arc

### Formal and Arrangement Insights

### Acoustic and Production Summary

### Aesthetic Characteristics
````

## 1) Whole-Track Overview

Write natural, information-dense prose that preserves the distinct whole-track information in
`global_caption`. Cover the following when supported by the evidence:

- genre/style and the audible characteristics supporting that description;
- mood, groove, approximate BPM, perceived meter, key (tonic and mode), and broad modal character;
- vocal identity, language, role, timbre, articulation, techniques, emotional expression, harmonies,
  and layering;
- primary sound sources, their acoustic/electronic/sampled character, performance techniques, and
  relationships among rhythm, bass, harmonic support, melody, and vocals;
- texture, overall development, timbral and frequency-balance impressions, dynamics, production,
  effects, and their audible contribution to the listening impression.

Use as many paragraphs as the supported information needs. Do not compress rich evidence into a
generic genre/mood sentence or an instrument list. Preserve meaningful relationships and effects,
such as how bass interacts with drums or how vocal layering changes perceived fullness. Merge
duplicate statements and remove filler rather than dropping distinct musical details merely for
brevity. Do not fill missing categories by inference or copy unsupported claims as established facts.
Keep estimated BPM approximate and preserve uncertainty or alternative interpretations of key,
instruments, and production techniques; never turn a tentative observation into a definite claim.

Consolidate stable facts here rather than repeating them in every structure interval. Whole-track
development and aesthetic relationships may be developed in Overall Summary without repeating this
section. The global-caption branch's 700-word limit does not apply to the complete final caption;
do not shorten structure descriptions or lyrics to fit that limit.

## 2) Structure Timeline

- When `structure.anomalies` is non-empty, add one compact `Analysis note:` line describing the
  deterministic merges or boundary clamp. This is diagnostic context, not a reason to reject the caption.
- Emit every normalized structure segment exactly once and in order.
- Preserve its timestamp format and lowercase label exactly.
- Write natural prose from the matching local annotation, using global evidence only for necessary
  context. Use as many paragraphs as the supported local information needs; do not impose a
  one-paragraph or one-to-two-sentence limit.
- Preserve distinct, useful local evidence when present: vocal presence, timbre, articulation,
  techniques and expression; sound sources, performance techniques and their musical roles;
  groove, texture, density and energy; local production or spatial effects and their audible impact;
  structural function, entries/exits, foreground-role changes, within-section development and
  transitions. Preserve relationships and audible causes, not just lists of attributes.
- Prioritize supported changes from the preceding section while retaining continuing features
  needed to understand this section on its own. A feature need not be new to be locally useful.
  If little changes, describe the continuity and retain the defining local evidence rather than
  reducing the section to an "unchanged" statement.
- Consolidate stable whole-track traits in the Overview, but briefly restate essential local context
  where needed. Remove duplicate wording and filler, not distinct information dimensions merely
  for brevity; avoid mechanically repeating full instrumentation or production inventories.
- These dimensions are evidence-retention guidance, not mandatory fields. Do not fill missing
  categories, invent changes, or turn uncertain observations into definite claims.
- Keep claims inside their source interval. Do not print lyrics in this section and do not infer
  exact structure–lyrics alignment.

## 3) Lyrics Timeline

If `sentence_lyrics.status="ok"`, reproduce the cues as standard SRT in their original order:
consecutive index, preserved timestamps, preserved one or two text lines, and one blank line between
cues. Do not translate, normalize, correct, merge, split, annotate, or embed them in structure.

If status is `no_lyrics`, output exactly `NO_LYRICS` beneath the heading.

Descriptive cue text accepted upstream, such as `[Instrumental]`, remains verbatim here and must
not be treated as a musical fact elsewhere.

## 4) Overall Summary

Use all four required third-level headings:

- **Emotional and Energy Arc:** trace rises, releases, plateaus, and declines with their supported
  audible causes.
- **Formal and Arrangement Insights:** summarize returns, contrasts, roles, and transformations
  without claiming that independent lyrics boundaries define structure.
- **Acoustic and Production Summary:** synthesize rhythmic, textural, timbral, dynamic, spatial, and
  effects identity.
- **Aesthetic Characteristics:** give a compact set of distinctive evidence-supported traits useful
  for understanding or visual planning, without inventing an MV concept.

Preserve distinct supported whole-track development and aesthetic information from `global_caption`
under these headings, integrating local evidence only within its proper scope. Explain relationships
and audible causes rather than restating the Overview paragraph-by-paragraph. Do not invent missing
development or resolve conflicting branches by silently choosing one.

## Final hard gate

Before responding, verify:

1. The sole input is one valid `music-caption-evidence` object.
2. Every structure segment appears exactly once and has no nested lyric cues.
3. Every SRT cue appears exactly once in the independent Lyrics Timeline.
4. Stable global facts are consolidated; each Structure Timeline caption preserves distinct, useful
   evidence from its matching annotation, including necessary continuing features and supported
   within-section development, while emphasizing changes without mechanical inventory repetition.
   No useful local information is lost merely to meet a brevity or paragraph-count preference;
   uncertainty is preserved and missing dimensions are not invented.
   Distinct supported global-caption details and relationships remain represented in the Overview
   or Overall Summary, with their original uncertainty, rather than lost through overcompression.
5. No local claim leaves its source interval and no timestamp exceeds the physical duration.
6. All four top-level sections and four summary subheadings are present.
7. The answer contains only the completed caption.
