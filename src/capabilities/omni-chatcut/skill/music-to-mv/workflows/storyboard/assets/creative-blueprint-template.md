# MV Creative Blueprint

This blueprint is a human-readable rendering of generation and editing decisions. Do not restate the upstream music analysis or add interpretation that is not used by a shot.

## Whole-Film Creative Direction

- Core premise: the concise idea, feeling, situation, or proposition that gives the full MV coherence.
- Driving thread: the relationship, question, behavior, journey, performance condition, visual process, or other development that carries the MV forward.
- Visual-world logic: how different people, places, materials, and image systems belong to the same film while remaining free to vary.
- Recurring motifs, when useful, and the exact way each changes across the film:

| Stage ID | Time | Visible state entering the stage | Development within the stage | Visible handoff to the next stage |
|---|---|---|---|---|

## Global Visual System

- Overall visual style and medium treatment:
- Composition and framing grammar:
- Lens and camera behavior:
- Lighting grammar:
- Color progression by music section:
- Texture and material language:
- Production-design rules:
- Wardrobe rules:
- Recurring visual elements and the exact shots where they change:
- Prohibited styles, visual defaults, text, logos, subtitles, and watermarks:

## Visual-Unit Allocation

A visual unit organizes one coherent development across one lyric cue, several adjacent cues, or an instrumental interval. It is not an editorial shot and does not determine provider request count.

| Unit ID | Start–end | Source kind | Exact lyric cue indices/text or instrumental evidence | Section caption evidence and design application | Development stage | Visual intent | Grouping decision and reason |
|---|---|---|---|---|---|---|---|

## Visual and Edit Outline

Use functional placeholders here; formal cast IDs and scene definitions are created only after the outline is complete.

| Visual unit(s)/time | Development stage | Visible situation or development | Provisional character roles | Provisional environment | Change from previous section | Handoff to next section | Editing behavior | Implementing shot IDs |
|---|---|---|---|---|---|---|---|---|

## Cast Assets Derived from the Outline

| ID | Role | Used by outline rows | Stable visible identity | Portrait-generation prompt | Identity constraints |
|---|---|---|---|---|---|

## Scene Definitions Derived from the Outline

| ID | Name | Used by outline rows | Setting/layout and usable zones | Lighting/palette/material | Wardrobe |
|---|---|---|---|---|---|

## Shot Skeleton

Complete the full chronological skeleton before expanding any shot content.

| Shot ID | Visual unit | Start–end | Music edit anchor | Type | Function | Scene/cast | Transition in and basis | Handoff to next |
|---:|---|---|---|---|---|---|---|---|

Allowed types are `dance`, `narrative`, `concept`, and `performance`. They may be combined freely when adjacent shots have a concrete musical, visual, action, gaze, shape, or spatial connection.

## Detailed Shot Design

Repeat the following block for every Shot Skeleton entry. Timing, type, function, scene, cast, transition, and handoff come from the skeleton and are not re-decided here.

### Shot `<global_index>`

- Literal image content:
- Composition and depth layout:
- Visible start state:
- Playable action / visual transformation stages:
- Visible end state:
- Camera size, angle, movement, and subject relationship:
- Shot-specific light, color, and material:
- Mouth and lyric behavior:
- Continuity inherited from previous shot:
- Generation constraints or risks:

For a dance shot, add:

- Dance style:
- Movement and groove quality:
- Connected movement stages:
- Musical accents and corresponding actions:

For a performance shot, add:

- Delivery mode:
- Visible gaze/breath/body/instrument action:
- Intensity change across the shot:

For a narrative shot, describe only the visible event and result needed by this shot. No fixed dramatic formula is required.

For a concept shot, describe the visible rule, variation, or transformation directly.

## Provider Request Mapping

Every row maps one editorial shot to one independent provider request. `Shot ID` and `Segment` must be
one-to-one; no request may include an internal edit boundary.

| Segment | Shot ID | Time | Duration | Assembly mode |
|---|---:|---|---:|---|
