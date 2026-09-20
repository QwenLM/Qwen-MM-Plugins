# Streamlined Authoring Contract

The execution payload keeps the core structure:

```json
{
  "video": {"duration_sec": 0},
  "creative_direction": {},
  "style_bible": {},
  "cast": [],
  "scenes": [],
  "segments": []
}
```

## Creative Direction

`creative_direction` contains execution compatibility, the concise whole-film treatment, the visual-unit allocation, and long-take metadata. Detailed visible design still lives on individual sub-shots.

```json
{
  "media_form": "live_action|2d_animation|3d_animation|mixed_media",
  "whole_film_treatment": {
    "core_premise": "one concise whole-film proposition",
    "driving_thread": "what develops across the full MV without requiring a conventional plot",
    "visual_world_logic": "how varied shots and locations remain part of one film",
    "recurring_motifs": [],
    "development_path": [
      {
        "id": "stage-1",
        "start_sec": 0.0,
        "end_sec": 30.0,
        "visible_state": "observable state entering this stage",
        "development": "what visibly changes during it",
        "handoff": "state delivered to the next stage"
      }
    ]
  },
  "visual_units": [
    {
      "id": "VU-001",
      "start_sec": 0.0,
      "end_sec": 8.0,
      "source_kind": "single_cue|merged_cues|instrumental",
      "development_stage_id": "stage-1",
      "lyric_cues": [
        {"index": 1, "start_sec": 0.0, "end_sec": 8.0, "text": "verbatim lyric"}
      ],
      "structure_refs": ["intro"],
      "section_caption_evidence": [
        {
          "structure_index": 0,
          "label": "intro",
          "start_sec": 0.0,
          "end_sec": 12.0,
          "overlap_start_sec": 0.0,
          "overlap_end_sec": 8.0,
          "caption": "verbatim upstream section caption",
          "design_application": "how this section evidence changes the visible design of this unit"
        }
      ],
      "visual_intent": "one coherent visible development",
      "grouping_reason": "required only for merged_cues"
    }
  ],
  "editorial_plan": {
    "long_take_threshold_sec": 8.0
  }
}
```

## Style Bible

```json
{
  "overall_visual_style": "",
  "color_palette": "",
  "film_look": "",
  "mood": "",
  "composition_grammar": "",
  "camera_grammar": "",
  "lighting_grammar": "",
  "material_language": "",
  "production_design": "",
  "wardrobe_rules": "",
  "recurring_elements": [],
  "prohibited_elements": []
}
```

## Cast and Scenes

Cast entries retain stable identity and `portrait_t2i_prompt`. Scene entries retain the prompt-ready setting, lighting, palette, material details, wardrobe, and optional blocking map that every affected shot needs.

## Sub-Shot

All timing, `camera`, `audio_sync`, `reference_image`, and cast-scene fields from the core storyboard payload remain. Add direct shot content:

```json
{
  "visual_unit_id": "VU-001",
  "shot_type": "dance|narrative|concept|performance",
  "shot_function": "concrete visible job",
  "shot_summary": "literal contents of this shot",
  "visual_design": {
    "image_content": "who/what is visible and their spatial relation",
    "composition": "framing and depth layout",
    "lighting_color_material": "shot-specific state",
    "visible_change": "observable start-to-end change",
    "handoff_to_next": "visible connection to the next shot"
  },
  "action_design": {
    "playable_verb": "",
    "start_physical_state": "",
    "action_steps": [],
    "end_physical_state": "",
    "generation_risk_factors": [],
    "fallback": ""
  },
  "movement_design": {
    "dance_style": "",
    "movement_quality": "",
    "action_stages": [],
    "accent_actions": [],
    "body_channels": "",
    "formation_start": "",
    "formation_end": "",
    "camera_protection": ""
  },
  "performance_design": {
    "delivery_mode": "",
    "visible_performance": "",
    "intensity_change": ""
  },
  "camera_geometry": {},
  "continuity": {},
  "transition_in": "cut|dissolve|fade_in|match_cut",
  "transition_basis": "required for match_cut"
}
```

`movement_design` is required only for dance shots. `performance_design` is required only for performance shots. Narrative and concept shots use the same direct `visual_design` and `action_design` contract without global story or concept engines.

Every sub-shot start has `audio_sync.edit_anchor`. Its source is `track_start`, `section_boundary`, `lyric_phrase_start`, `lyric_phrase_end`, or the documented `structure_internal_split` exception. A long take adds at least two `audio_sync.internal_music_stages` and a concise `long_take_rationale`.

## One-Shot Request Envelope

Every `segment` is one provider request and contains exactly one editorial `sub_shot`. Use
`assembly_mode: single_take_i2v`. The segment and shot have identical absolute start, end, duration,
scene, and cast values. `shot_windows` contains exactly one entry whose local time is
`[0, segment.duration_sec]` and whose `sub_global_index` points to that shot.

The selected video provider generates no internal edit. All boundaries and transitions between segments belong to local
assembly. Intermediate visual or action stages inside a long take remain continuous and do not create
additional editorial shots.
