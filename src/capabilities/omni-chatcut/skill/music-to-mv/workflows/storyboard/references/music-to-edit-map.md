# Music Evidence for Editing

Use the completed music analysis directly while writing the timed shot plan. Do not create a second music summary. Use lyric phrasing and structure changes as the primary basis for editorial-shot boundaries.

## Visual units before shots

Create a contiguous visual-unit allocation before the visual/edit outline and shot skeleton. A visual
unit is an authoring unit: it gives one coherent visible development to one lyric cue, several
adjacent lyric cues, or a lyric-free interval. It may later produce one shot or several separate
shots. It never authorizes an internal provider cut and never changes the sentence-level subtitle
timeline.

Default to one lyric cue per unit. Optionally merge adjacent cues when separate treatment would create
very short, repetitive, or semantically fragmented ideas and the cues can sustain one visible
development. A useful merged group is usually two to four consecutive cues, but use creative
judgment rather than a quota. Do not merge across a structure boundary, meaningful pause, speaker
change, semantic reversal, or a change that deserves its own visual development.

For every unit record:

- stable ID and exact start/end time
- `source_kind`: `single_cue`, `merged_cues`, or `instrumental`
- exact cue indices, timestamps, and verbatim text; keep this list empty for an instrumental unit
- overlapping structure references
- `section_caption_evidence` for every overlapping structure section, containing its zero-based
  `structure_index`, label, exact source start/end time, exact unit-overlap start/end time, verbatim
  upstream section caption, and a concise `design_application`
- the whole-film development stage it belongs to
- one concrete visual intent that advances that stage
- a grouping reason for merged cues

Visual units cover the full timeline without gaps or overlaps. Preserve the accepted SRT unchanged;
grouping affects visual authorship only. A visual unit may exceed a provider's duration limit because
the shot skeleton can divide it into multiple editorial shots.

`structure_refs` remains a compact list of labels, but repeated labels such as verse or chorus are not
unique evidence. `structure_index` identifies the exact upstream section. Copy `caption` without
rewriting it. Write `design_application` separately to state how the section's arrangement, vocal,
timbre, or density evidence affects this unit's image, action, movement, performance, or camera plan.
Preserve all lines and paragraphs of the source caption. In the application note, use relevant
continuing features, performance and production details, and within-section development alongside
section-to-section changes. Select evidence that informs the visible design without forcing every
musical attribute into a visual counterpart. A section-level observation is not evidence that an
event occurs in a particular sub-interval: preserve uncertainty, and treat any authored timing of
an untimed development as a creative choice rather than a detected musical event.
This evidence guides authoring only; downstream video and QC prompts consume the resulting shot
design rather than the raw section caption.

## Editorial shot anchors

The normal edit-anchor sources are:

- `track_start`
- `section_boundary`
- `lyric_phrase_start` or `lyric_phrase_end`

Prefer lyric-phrase boundaries inside a structure section and structure boundaries when the section changes. A structure section does not have to equal one shot. If it is too long to serve as one editorial shot and contains no usable lyric boundary, divide it into multiple shots and label each additional start `structure_internal_split`.

For `structure_internal_split`, set `source_time_sec` to the authored split time, identify the enclosing structure interval in `evidence`, and explain in `selection_reason` that the interval was divided because it was too long and provided no usable lyric boundary. This is an editorial subdivision, not a claim that the caption detected a musical event at that timestamp.

Every selected shot start is copied to `audio_sync.edit_anchor` with source, source timestamp, concise evidence, and selection reason. Do not invent finer-grained musical evidence that is absent from the upstream caption.

Use the selected lyric or structure anchor to decide whether the next image should cut, hold, change action, change scale, or transition. Dance may respond through movement; narrative through visible action/reaction; concept through material or environment change; performance through lyric delivery, gaze, gesture, or instrument action. These shot types may appear in any sequence without a declared global mix.

Long takes list at least two timestamped `audio_sync.internal_music_stages`. Provider generation duration never determines editorial shot duration.
