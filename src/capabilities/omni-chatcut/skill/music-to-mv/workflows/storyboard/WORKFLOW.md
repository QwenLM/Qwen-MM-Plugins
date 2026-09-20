<!-- Internal workflow: loaded by the root qwen-mm-plugins-omni-chatcut-music-to-mv Skill; not independently discoverable. -->

# Storyboard Workflow

Turn completed music analysis and a user brief into a generation-ready visual style, exact timed shots, and valid storyboard JSON. Store only decisions that directly affect image assets, video prompts, shot timing, transitions, continuity, segment packaging, or execution review.

## Scope

The storyboard may use four shot types in any organic combination:

- `dance`: connected choreographed movement is the visible content
- `narrative`: a visible event, action, reaction, or result advances what is happening on screen
- `concept`: a visual rule, observation, material change, or environment transformation is the content
- `performance`: a singer or musician performs through mouth, gaze, breath, gesture, body, or instrument

These are per-shot functions, not global MV modes. Do not choose a primary MV type, declare a type mix, or create separate type engines. Decide the sequence directly in the blueprint and derive any runtime statistics from the finished shots.

`creative_direction.media_form` remains downstream compatibility metadata. Fill it from an explicit user constraint or the concrete style; do not run a separate media-form selection exercise.

Do not execute generation APIs. Hand the validated storyboard JSON to an executor compatible with its media form.

## Inputs

- completed music analysis containing duration, structure timeline, lyric timeline, and musical changes
- source audio
- user brief and concrete constraints

Use the analysis as evidence while designing sections, shot boundaries, actions, and transitions. Do not produce or fill a separate music-reading summary.
Read the complete Overview, Overall Summary, and every structure caption, including all lines and
paragraphs. Preserve the section text during normalization and evidence copying.

When upstream Markdown needs normalization, use:

```bash
python3 <workflow-root>/scripts/normalize_music_analysis.py <analysis.md> --output <music_map.json>
```

Read [input-contract.md](references/input-contract.md) only when input completeness or timestamp precedence is unclear. Read [music-to-edit-map.md](references/music-to-edit-map.md) while placing shots.

## Outputs

Write:

1. `<title>_creative_blueprint.md`
2. `<title>_storyboard.json`
3. `<title>_validation_report.json`
4. `<title>_continuity_report.json`

The storyboard JSON is the downstream source of truth. The blueprint is its human-readable shot plan, not a separate essay about author intent.

## Workflow

### 1. Define the Whole-Film Creative Direction

For Seedance, if the user has specified a character asset, first read its available local-catalog
description or user-provided appearance information and use those traits as creative constraints
before defining the direction and outline. An asset ID alone is not appearance evidence. If no
character is specified, continue selecting assets from the outline's role requirements in step 5.

Before allocating visual units or shots, define a concise `creative_direction.whole_film_treatment`:

- `core_premise`: the idea, feeling, situation, or proposition that gives the full MV coherence
- `driving_thread`: what develops across the film; it may be a relationship, question, behavior, journey, performance condition, visual process, or spatial progression and need not be a conventional plot or conflict
- `visual_world_logic`: how varied characters, scenes, materials, and image systems remain part of the same film without limiting the number of locations
- `recurring_motifs`: only motifs whose appearances and changes can be assigned to later stages or shots
- `development_path`: ordered time-covering stages that state the visible incoming state, development, and handoff

The treatment must guide downstream visual units and shots, not become an interpretive essay. Do not impose a narrative-runtime ratio, required story formula, fixed location count, or mandatory motif count.

### 2. Define the Global Visual System

Complete the global-style portion of [creative-blueprint-template.md](assets/creative-blueprint-template.md). Define only promptable visual decisions:

- overall visual style and medium treatment
- composition and framing grammar
- lens and camera behavior
- lighting grammar
- color progression tied to music sections
- texture and material language
- production design and wardrobe rules
- recurring visual elements only when their appearances and changes are assigned to exact shots
- prohibited visual defaults, text, logos, watermarks, or unwanted styles

Write these decisions into `style_bible`. Read [aesthetic-direction.md](references/aesthetic-direction.md).

### 3. Allocate Visual Units

Read [music-to-edit-map.md](references/music-to-edit-map.md). Build `creative_direction.visual_units` as a contiguous full-song allocation before deciding editorial shots.

Default to one lyric cue per visual unit. Merge adjacent cues only when they can support one coherent visible development and separate treatment would create short, repetitive, or semantically fragmented material. Preserve every cue's index, timestamp, and verbatim text; never change the accepted subtitle timeline. Create explicit instrumental units for substantial lyric-free intervals.

Each visual unit records its source kind, exact lyric evidence, the complete upstream caption for every
overlapping structure section, development-stage ID, concrete visual intent, and—when cues are
merged—the reason grouping improves the visual plan. For each overlapping section, preserve its
zero-based structure index, label, source time range, exact caption, overlap with the unit, and a concise
`design_application` stating how that music evidence changes this unit's visible design. Do not replace
the source caption with the application note. A unit is not a shot: it may map to one or several
editorial shots, and it does not permit the selected video provider to create internal cuts.
Use relevant continuing features and within-section development as well as changes between sections
when writing `design_application`; do not reduce the evidence to change points alone. Preserve
uncertainty and do not assign exact musical-event timestamps to untimed descriptions.

### 4. Write the Visual and Edit Outline

Before dividing individual shots, map the full song through its visual units and development stages. For each outline row decide:

- the visible situation or visual development
- provisional character roles needed by that section, such as lead performer, second figure, ensemble, or no cast
- provisional environment needs, such as fixed interior, open exterior, stage, or changing travel location
- the change from the previous section
- the image, action, movement, or performance state handed to the next section
- the approximate editing behavior: sustained, sparse, moderate, or dense

This is an image-and-edit outline, not a required screenplay or a second music summary. It must make the whole-film development visible without forcing a conventional plot. Use functional placeholders rather than final cast/scene IDs or asset prompts. Every visual unit belongs to an outline row, and every outline row must later cite the exact shot IDs that implement it.

### 5. Refine Cast Assets and Scene Definitions from the Outline

Derive the formal asset inventory from the completed outline. Do not add a cast member or scene unless at least one outline row needs it.

When the selected video provider is Seedance, skip identity-image generation and use Ark image
assets before finalizing the cast. Follow [character selection](../../references/seedance-character-search.md)
and [Seedance bindings](../video-generation/references/seedance.md):

1. Preserve explicit user asset IDs or existing project selections. Normalize bare `asset-...` IDs
   to `asset://asset-...`. They may come from the public library or the user's completed Ark upload.
2. For each unspecified role, derive tags and keywords from the outline and run
   [local search](../../scripts/search_local_characters.py) against the bundled pool. Inspect a short
   list and choose the best fit; do not request permission just to search or propose a character.
3. Present the candidate's name, relevant tags, biography, image asset ID and selection reason.
   Briefly explain the two override paths: upload/authorize an image in Ark and provide its asset ID,
   or choose a public-library image and copy its ID. Treat this as an option, not a required question.
4. Review available previews before finalizing visible identity. Local `--details` provides official
   descriptions, not visual inspection. Use previews/descriptions supplied by the user; website
   search and preview retrieval are manual user actions. Do not query the website for assets. If a preview is missing,
   keep the proposed appearance provisional and continue independent storyboard work.
5. Bind the chosen image URI in `providers.seedance.official_identity_assets`. Record its source
   (pool/public library/user upload), choice rationale and available appearance evidence in the cast
   planning notes. Keep explicit user choices; ask for an alternative only if necessary because the
   asset is unusable or no suitable candidate can be found.

Portrait prompt fields may remain for schema compatibility but must not trigger image generation
for Seedance. This pipeline consumes existing image assets; guide uploads in the official console,
not through an automatic registration step. Missing bindings must be resolved before paid execution.

Replace provisional character roles with stable cast IDs. For each cast member define role, visible identity, appearance, portrait-generation prompt, and any identity constraints needed by the configured image and video providers.

Replace provisional environments with stable scene IDs. For each scene define name, setting, lighting, palette, material details, usable layout or blocking zones, and wardrobe. These text definitions are used directly in each shot's provider prompt; shot-level composition and local environment details remain free to vary with the shot.

Record which outline rows use every cast member and scene. Read [top-level-design.md](references/top-level-design.md). Use [blocking-camera-design.md](references/blocking-camera-design.md) when a shot depends on exact staging or movement through space.

### 6. Build the Shot Skeleton

Divide the full timeline into editorial shots before writing detailed image prompts. For every shot lock only:

- `global_index` and absolute start/end time
- the `visual_unit_id` whose visible development this shot implements
- `audio_sync.edit_anchor`, normally taken from a lyric-phrase or structure boundary; use a declared structure-internal split only when a long structure interval has no usable lyric boundary
- one `shot_type`: `dance`, `narrative`, `concept`, or `performance`
- a concrete `shot_function`
- scene and exact cast present
- transition into the shot and its visible or musical basis
- the intended handoff to the next shot

Review the skeleton as a complete sequence. Its shots must cover the full timeline without gaps, implement every visual unit and outline row, advance the assigned whole-film stage, and combine shot types through music, action, image, gaze, shape, or spatial continuity rather than mechanical alternation. Several shots may implement one visual unit, but one shot cannot straddle visual-unit boundaries.

Read [shot-type-grammar.md](references/shot-type-grammar.md). Do not write detailed content until the outline, boundaries, types, and functions are coherent.

### 7. Expand Type-Specific Shot Content

Now fill the visible content required by each skeleton entry:

- `dance`: specific style, movement quality, connected action stages, musical accents, body channels, and formation state
- `narrative`: the concrete visible event, behavior, reaction, or result in this shot
- `concept`: the visible rule, observation, variation, or transformation
- `performance`: delivery mode plus visible mouth, gaze, breath, body, gesture, or instrument performance

Narrative shots do not require a predefined story formula. They may be causal, fragmentary, nonlinear, observational, ambiguous, or purely behavioral. State only what must be visibly generated and how its result connects to neighboring shots.

For dance shots, read [dance-choreography.md](references/dance-choreography.md) and put the required information directly in `movement_design`.

### 8. Add Generation and Editing Detail

Follow [authoring-contract.md](references/authoring-contract.md). Expand every skeleton entry into one complete generation-ready `sub_shot`.

Required shot-level groups are:

- `visual_design`: literal content, composition, light/color/material, and visible change
- `action_design`: playable action, action stages, and physical start/end states
- `camera` and `camera_geometry`
- `continuity`
- `audio_sync.edit_anchor`
- `reference_image.cast_scene_table`
- `movement_design` for dance shots
- `performance_design` for performance shots

Use [continuity-editing.md](references/continuity-editing.md) to make adjacent shots agree on wardrobe, props, blocking, gaze, screen direction, and action state. Internal IDs organize JSON only and must not enter provider prompt prose.

Review the sequence for meaningful visual variation across adjacent shots and different scenes while
preserving the global style and necessary continuity. Vary shot size, composition, viewing angle,
subject action, spatial relationships, or lighting/color; do not merely change backgrounds while
reusing the same pose and camera movement. Intentional repetition must serve a clear visual callback
or progression.

### 9. Wrap Each Shot as One Provider Segment

Create exactly one `segment` for every editorial shot. Each segment must:

- use `assembly_mode: single_take_i2v`
- contain exactly one `sub_shot`
- contain exactly one `shot_window` spanning local time `[0, segment.duration_sec]`
- copy the shot's absolute start, end, duration, scene, and cast without grouping it with neighboring shots

The segment is only a request envelope. The selected provider generates the one continuous shot inside it; the local
assembler creates every boundary between segments. Never ask the provider to create an internal cut, change to
another editorial shot, or approximate several shot durations inside one request. The shared authoring
contract permits 1–15 seconds; provider-specific validation may impose a narrower limit. Merge an unusably short lyric cue with an
adjacent semantically coherent cue before detailed shot design; split longer material at an accepted
lyric, structure, or documented structure-internal anchor.

A long continuous shot may evolve through controlled action or visual states, but those states are not
additional editorial shots. It must contain visible development tied to at least two internal music moments.

### 10. Validate the Handoff

Run:

```bash
python3 <workflow-root>/scripts/validate_storyboard.py <storyboard.json> \
  --music-map <normalized-music-map.json> \
  --report <validation_report.json> \
  --continuity-report <continuity_report.json> \
  --artifact-dir <authoring-output-directory>
```

Fix structural and execution-readiness errors before handoff. Warnings identify details that may weaken generation or editing but do not introduce subjective scoring systems.

For an existing grouped storyboard, run `scripts/split_provider_segments.py <old.json> <new.json>`.
This copies every existing editorial shot into its own request envelope without changing creative
content or timing. Treat the output as a new authoring artifact and run both validators again.

## Hard Rules

- Music analysis is input evidence, not a second report to rewrite.
- There is no global MV type selection or declared type mix.
- Define the whole-film creative direction before visual units, outline rows, formal asset design, or shots; incorporate available user-specified character information first.
- The whole-film direction provides coherence and development without imposing a plot formula, narrative quota, fixed location count, or fixed motif count.
- Visual units cover the full timeline and preserve exact lyric cues; optional cue grouping never changes subtitles or provider-request boundaries.
- Every visual unit maps to actual shots, and every shot belongs wholly to one visual unit and advances its development stage.
- The outline defines provisional character and environment needs before cast portraits and formal scene definitions are designed.
- Every formal cast member and scene traces back to at least one outline row.
- Complete the visual/edit outline and full Shot Skeleton before expanding individual shot content.
- Every outline row maps to actual shot IDs; every detailed shot preserves its skeleton timing, type, function, cast, scene, and transition.
- Every shot has exactly one functional shot type; the finished sequence may combine all four types.
- The blueprint and JSON state exactly what is visible in every shot and how it connects to adjacent shots.
- Global style, cast identity, scene state, shot content, action, camera, timing, and transition must be usable by downstream generation or editing.
- Narrative shots are not forced into a fixed dramatic template.
- Scene definitions and shot text control environments; person references control identity; shot text also controls action, wardrobe, blocking, camera, and timing.
- Generated provider windows do not redefine editorial cuts.
- Every editorial shot is generated by one and only one provider request; the provider never owns an internal edit boundary.

## Resources

- [input-contract.md](references/input-contract.md): upstream music-analysis shape and timing precedence
- [music-to-edit-map.md](references/music-to-edit-map.md): optional lyric grouping, visual-unit allocation, and evidence-based shot boundaries
- [aesthetic-direction.md](references/aesthetic-direction.md): promptable global visual system
- [top-level-design.md](references/top-level-design.md): reusable cast identities and prompt-ready scene definitions
- [shot-type-grammar.md](references/shot-type-grammar.md): four per-shot functions and direct fields
- [dance-choreography.md](references/dance-choreography.md): generation-ready dance movement design
- [blocking-camera-design.md](references/blocking-camera-design.md): playable spatial action and camera geometry
- [continuity-editing.md](references/continuity-editing.md): adjacent-shot state and edit continuity
- [authoring-contract.md](references/authoring-contract.md): streamlined JSON contract
- [creative-blueprint-template.md](assets/creative-blueprint-template.md): human-readable executable shot plan
- `scripts/normalize_music_analysis.py`: optional input normalizer
- `scripts/validate_storyboard.py`: execution-readiness and continuity validator
- `scripts/split_provider_segments.py`: structural migration from grouped requests to one-shot requests
